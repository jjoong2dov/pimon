"""pimon 을 가짜 파이로 띄워 SIGHUP 을 연달아 보낸 뒤 SIGTERM. N번 되풀이해 멈춘 횟수를 센다.
사용: python3 hup_stress.py 저장소 N 명령...   (명령 안의 {cfg} {root} 를 바꿔 끼운다)"""
import os, signal, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

repo, n, cmd = Path(sys.argv[1]).resolve(), int(sys.argv[2]), sys.argv[3:]
sys.path.insert(0, str(repo / "fake"))
import make_fake
hung = 0
for i in range(n):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        root = make_fake.make(t / "pi", fresh=True)
        port = 19100 + i % 50
        cfg = t / "pimon.toml"
        tpl = os.environ["PIMON_TPL"]
        cfg.write_text(tpl.format(root=root, port=port, state=t / "led.json", statedir=t / "state"))
        args = [a.format(cfg=cfg, root=root) for a in cmd]
        p = subprocess.Popen(args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1); break
            except OSError:
                time.sleep(0.1)
        for _ in range(5):
            p.send_signal(signal.SIGHUP)
        p.send_signal(signal.SIGTERM)
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            hung += 1
            p.send_signal(signal.SIGABRT)   # python -X faulthandler 면 스레드 스택을 찍는다
            try:
                out = p.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                p.kill(); out = p.communicate()[0]
            if hung == 1 and os.environ.get("SHOW_FIRST"):
                print("---- 처음 멈춘 회의 출력 ----\n" + out[-6000:])
print(f"{n}회 중 멈춤 {hung}회")
