#!/bin/sh
# PC 에서 파이로 pimon 올리기.  사용: sh deploy/deploy.sh pi@파이주소
#
# 파이 쪽 배치:
#   /opt/pimon/releases/<시각>/   한 번 올릴 때마다 새 폴더 (pimon 코드 + pimon.service)
#   /opt/pimon/current            지금 쓰는 판을 가리키는 링크
#   /opt/pimon/previous           바로 전 판을 가리키는 링크 (rollback.sh 가 씀)
# 새 판은 다른 폴더에서 검사를 마친 뒤에야 current 로 바꾼다. 다시 시작한 뒤 응답이 없으면
# 자동으로 전 판으로 되돌린다.
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
(cd "$D" && python3 -m pimon check-config --config /etc/pimon/pimon.toml)

OLD=$(readlink /opt/pimon/current || true)
switch() {   # $1 판으로 current 를 바꾸고 서비스 다시 시작
    sudo ln -sfn "$1" /opt/pimon/current.new && sudo mv -T /opt/pimon/current.new /opt/pimon/current
    sudo cp "$1/deploy/pimon.service" /etc/systemd/system/pimon.service
    sudo systemctl daemon-reload
    sudo systemctl enable -q pimon
    sudo systemctl restart pimon
}
switch "$D"
ok=0
for i in 1 2 3 4 5; do
    sleep 2
    if (cd "$D" && python3 -m pimon health --config /etc/pimon/pimon.toml); then ok=1; break; fi
done
if [ $ok = 1 ]; then
    [ -n "$OLD" ] && sudo ln -sfn "$OLD" /opt/pimon/previous
    echo "배포 완료: $REL"
    exit 0
fi
echo "새 판이 응답하지 않음. 최근 기록:"
sudo journalctl -u pimon -n 20 --no-pager || true
if [ -n "$OLD" ]; then
    echo "전 판으로 되돌림: $OLD"
    switch "$OLD"
fi
exit 1
REMOTE
