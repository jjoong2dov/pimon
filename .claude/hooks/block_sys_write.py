#!/usr/bin/env python3
"""PreToolUse 훅: Bash 명령이 진짜 /sys, /proc 에 쓰려 하면 막는다.

Claude Code 가 stdin 으로 넘기는 JSON 의 tool_input.command 를 읽는다.
막을 때는 exit 2 와 함께 이유를 stderr 에 쓴다 (Claude 에게 전달된다).

잡는 것:
  - 리다이렉션 대상:  > >> >| &> 2> 등
  - 쓰기 명령의 대상: tee, truncate, touch, rm, cp/mv/install/ln (마지막 인자, -t), dd of=
  - sysctl -w / --write
  - bash -c / sh -c 안의 명령 (다시 검사)
  - cd 로 옮긴 뒤의 상대 경로 (cd /sys/... && echo > trigger)
  - python 한 줄에서 open('/sys/...', 'w') 나 Path('/sys/...').write_text(...)

fake/ 아래 가짜 파이처럼 /sys, /proc 로 시작하지 않는 경로는 통과한다.
명령 문자열만 보는 검사라 변수($ROOT/sys), 스크립트 파일 안의 쓰기 등은 못 잡는다.
"""

import json
import os
import re
import shlex
import sys

PROTECTED = ("/sys", "/proc")

PUNCT = "();<>|&\n"
WRAPPERS = {"sudo", "env", "nohup", "exec", "command", "time", "nice", "stdbuf", "timeout"}
SHELLS = {"sh", "bash", "dash", "zsh"}
DEST_LAST = {"cp", "mv", "install", "ln"}
ALL_ARGS = {"tee", "truncate", "touch", "rm"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

PY_WRITE = re.compile(
    r"""open\(\s*['"](/[^'"]*)['"]\s*,\s*['"][^'"]*[wax+]"""
    r"""|Path\(\s*['"](/[^'"]*)['"]\s*\)\s*\.\s*(?:write_text|write_bytes|open\(\s*['"][^'"]*[wax+])"""
)
# 따옴표가 맞지 않아 shlex 가 실패할 때 쓰는 거친 검사
FALLBACK = re.compile(r"(?:>|\btee\b[^;&|]*?)\s*['\"]?/+(?:sys|proc)(?:/|\b)")


def is_protected(path, cwd):
    if not path or path.startswith(("$", "~")):
        return False
    full = path if path.startswith("/") else os.path.join(cwd, path)
    full = "/" + os.path.normpath(full).lstrip("/")
    return any(full == p or full.startswith(p + "/") for p in PROTECTED)


def tokenize(cmd):
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=PUNCT)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


def is_op(tok):
    return tok != "" and all(c in PUNCT for c in tok)


def split_commands(tokens):
    """연산자 기준으로 단순 명령 (낱말들, 쓰기 리다이렉션 대상들) 목록으로 나눈다."""
    cmds, words, redirs = [], [], []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if is_op(tok) and (">" in tok or "<" in tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if ">" in tok:
                # 'echo 1>/x' 의 1 처럼 바로 앞 숫자는 fd 번호다
                if words and words[-1].isdigit():
                    words.pop()
                if not (tok.endswith("&") and (target.isdigit() or target == "-")):
                    redirs.append(target)
            i += 2
            continue
        if is_op(tok):
            cmds.append((words, redirs))
            words, redirs = [], []
        else:
            words.append(tok)
        i += 1
    cmds.append((words, redirs))
    return cmds


def strip_wrappers(words):
    """FOO=1, sudo, env, timeout 10 같은 앞머리를 떼고 실제 명령부터 돌려준다."""
    words = list(words)
    while words:
        if ASSIGN.match(words[0]):
            words.pop(0)
        elif words[0] in WRAPPERS:
            words.pop(0)
            while words and (words[0].startswith("-") or ASSIGN.match(words[0])
                             or re.match(r"^[0-9.]+[smhd]?$", words[0])):
                words.pop(0)
        else:
            break
    return words


def write_targets(words):
    """단순 명령이 쓰는 파일 경로들."""
    name = os.path.basename(words[0])
    args = words[1:]
    plain = [a for a in args if not a.startswith("-")]
    if name in ALL_ARGS:
        return plain
    if name in DEST_LAST:
        out = []
        for i, a in enumerate(args):
            if a in ("-t", "--target-directory") and i + 1 < len(args):
                out.append(args[i + 1])
            elif a.startswith("--target-directory="):
                out.append(a.split("=", 1)[1])
        if plain:
            out.append(plain[-1])
        return out
    if name == "dd":
        return [a[3:] for a in args if a.startswith("of=")]
    return []


def is_sysctl_write(args):
    return any(a in ("-w", "--write") or
               (a.startswith("-") and not a.startswith("--") and "w" in a)
               for a in args)


def check(cmd, cwd, depth=0):
    """막을 이유(문자열)를 돌려주고, 괜찮으면 None."""
    m = PY_WRITE.search(cmd)
    if m:
        path = m.group(1) or m.group(2)
        if is_protected(path, cwd):
            return f"파이썬 코드로 {path} 에 쓰기"
    try:
        tokens = tokenize(cmd)
    except ValueError:
        if FALLBACK.search(cmd):
            return "명령을 해석할 수 없고 /sys 또는 /proc 쓰기로 보임"
        return None

    for words, redirs in split_commands(tokens):
        for t in redirs:
            if is_protected(t, cwd):
                return f"리다이렉션으로 {t} 에 쓰기"
        words = strip_wrappers(words)
        if not words:
            continue
        name = os.path.basename(words[0])
        if name == "cd":
            target = words[1] if len(words) > 1 else "~"
            if not target.startswith(("$", "~", "-")):
                cwd = os.path.normpath(os.path.join(cwd, target))
            continue
        if name == "sysctl" and is_sysctl_write(words[1:]):
            return "sysctl -w 로 /proc/sys 에 쓰기"
        if name in SHELLS and depth < 3:
            for i, a in enumerate(words[1:-1], start=1):
                if a.startswith("-") and not a.startswith("--") and "c" in a:
                    reason = check(words[i + 1], cwd, depth + 1)
                    if reason:
                        return reason
                    break
        for t in write_targets(words):
            if is_protected(t, cwd):
                return f"{name} 로 {t} 에 쓰기"
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError) as e:
        print(f"block_sys_write: 입력 JSON 을 읽지 못함: {e}", file=sys.stderr)
        return 1
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    reason = check(cmd, cwd)
    if reason:
        print(
            f"막음: 진짜 /sys 또는 /proc 에 쓰는 명령입니다 ({reason}). "
            "이 저장소에서는 fake/ 아래 가짜 파이 경로를 쓰세요. "
            "실제 파이에 써야 한다면 사람에게 확인받으세요.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
