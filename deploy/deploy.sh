#!/bin/sh
# PC 에서 파이로 pimon 올리기.  사용: sh deploy/deploy.sh pi@파이주소
#
# 파이 쪽 배치:
#   /opt/pimon/releases/<시각>/   한 번 올릴 때마다 새 폴더 (pimon 코드 + pimon.service)
#   /opt/pimon/current            지금 쓰는 판을 가리키는 링크
#   /opt/pimon/previous           바로 전 판을 가리키는 링크 (rollback.sh 가 씀)
# 새 판은 다른 폴더에서 검사를 마친 뒤에야 current 로 바꾼다. 다시 시작한 뒤 응답이 없거나
# 서비스가 죽으면 자동으로 전 판으로 되돌린다. 설정 검사 실패, 처음 배포 실패, 정상으로 되돌린 경우에는
# 새 판 폴더를 지운다(전 판도 응답하지 않으면 살펴보도록 남긴다). 성공하면 current, previous 와
# 그 밖의 가장 최근 판 하나만 남기고 옛 판을 지운다.
#
# 종료 코드:
#   0  배포 완료. 새 판이 돌고 있다
#   1  배포하지 않았거나 실패했지만, 파이는 전 판으로 정상 응답한다
#      (단위 시험·설정 검사 실패는 아무것도 바꾸지 않음. 새 판 실패는 전 판으로 되돌렸고 응답 정상)
#   2  손으로 확인 필요. 파이에서 pimon 이 제대로 돌지 않는다
#      (전 판도 응답하지 않음, 또는 처음 배포가 실패해 서비스를 멈춤)
#   그 밖의 값은 ssh/scp 가 돌려준 것(255 는 접속 실패)
set -eu
HOST=${1:?사용: sh deploy/deploy.sh pi@파이주소}
cd "$(dirname "$0")/.."

.venv/bin/python -m pytest -q || { echo "단위 시험 실패: 올리지 않음"; exit 1; }

REL=$(date +%Y%m%d-%H%M%S)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -C . -czf "$TMP/pimon.tgz" pimon deploy/pimon.service pimon.toml.example
scp -q "$TMP/pimon.tgz" "$HOST:/tmp/pimon-$REL.tgz"

ssh "$HOST" REL="$REL" sh -s <<'REMOTE'
set -eu
D=/opt/pimon/releases/$REL
python3 -c 'import sys; assert sys.version_info >= (3, 11)' || { echo "python3 3.11 이상 필요"; exit 1; }
sudo mkdir -p "$D" /etc/pimon
sudo tar -C "$D" -xzf "/tmp/pimon-$REL.tgz"
rm -f "/tmp/pimon-$REL.tgz"
[ -f /etc/pimon/pimon.toml ] || sudo cp "$D/pimon.toml.example" /etc/pimon/pimon.toml

# 새 판으로 설정 검사. 실패하면 아무것도 바꾸지 않고 끝낸다.
(cd "$D" && python3 -m pimon check-config --config /etc/pimon/pimon.toml) ||
    { echo "설정 검사 실패: 바꾸지 않음"; sudo rm -rf "$D"; exit 1; }

OLD=$(readlink /opt/pimon/current || true)

# 여기부터는 set -e 를 끈다. 어느 단계에서 실패하든 아래 되돌리기까지 가야 한다.
set +e
switch() {   # 지금 판을 멈추고 $1 판으로 current 를 바꾼 뒤 시작. 모두 성공해야 0
    # 먼저 멈춰야 지금 판의 ExecStopPost(LED 되돌리기)가 지금 판의 unit 과 코드로 돈다
    { [ ! -e /etc/systemd/system/pimon.service ] || sudo systemctl stop pimon; } &&
    sudo ln -sfn "$1" /opt/pimon/current.new && sudo mv -T /opt/pimon/current.new /opt/pimon/current &&
    sudo cp "$1/deploy/pimon.service" /etc/systemd/system/pimon.service &&
    sudo systemctl daemon-reload &&
    sudo systemctl enable -q pimon &&
    sudo systemctl start pimon
}
restarts() { systemctl show -p NRestarts --value pimon; }
healthy() {  # 10초 안에 /status 가 응답하면 0. 그 전에 서비스가 죽거나 다시 시작되면 바로 1
    base=$(restarts) || { echo "systemd 조회 실패"; return 1; }
    for i in 1 2 3 4 5; do
        sleep 2
        state=$(systemctl is-active pimon)
        now=$(restarts) || { echo "systemd 조회 실패"; return 1; }
        if [ "$state" != active ] || [ "$now" != "$base" ]; then
            echo "서비스가 죽음: $state, 재시작 $base -> $now"
            return 1
        fi
        (cd /opt/pimon/current && python3 -m pimon health --config /etc/pimon/pimon.toml) && return 0
    done
    return 1
}
prune() {  # current, previous 와 그 밖의 가장 최근 판 하나만 남긴다
    cur=$(readlink /opt/pimon/current)
    prev=$(readlink /opt/pimon/previous)
    keep=1
    for r in $(ls -1r /opt/pimon/releases); do
        case "/opt/pimon/releases/$r" in "$cur"|"$prev") continue ;; esac
        if [ "$keep" -gt 0 ]; then keep=$((keep - 1)); continue; fi
        sudo rm -rf "/opt/pimon/releases/$r"
    done
}

if switch "$D" && healthy; then
    [ -n "$OLD" ] && sudo ln -sfn "$OLD" /opt/pimon/previous
    prune
    echo "배포 완료: $REL"
    exit 0
fi
echo "새 판이 제대로 뜨지 않음. 최근 기록:"
sudo journalctl -u pimon -n 20 --no-pager
if [ -z "$OLD" ]; then
    echo "처음 배포라 되돌릴 판이 없음. 서비스를 멈추고 새 판을 지움"
    sudo systemctl disable --now pimon
    # current 도 지운다. 남기면 다음 배포가 지워진 판을 전 판으로 알고 되돌리려 한다
    sudo rm -f /opt/pimon/current && sudo rm -rf "$D"
    exit 2
fi
echo "전 판으로 되돌림: $OLD"
if switch "$OLD" && healthy; then
    sudo rm -rf "$D" && echo "실패한 판 지움: $D"
    echo "전 판으로 되돌렸고 응답 정상"
    exit 1
fi
echo "전 판도 응답하지 않음. 손으로 확인 필요: sudo journalctl -u pimon"
exit 2
REMOTE
