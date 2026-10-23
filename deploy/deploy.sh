#!/bin/sh
# PC 에서 파이로 pimon 올리기.  사용: sh deploy/deploy.sh pi@파이주소
# 올리기 전 /opt/pimon 을 /opt/pimon.prev 로 남겨 두므로 rollback.sh 로 되돌릴 수 있다.
set -eu
HOST=${1:?사용: sh deploy/deploy.sh pi@파이주소}
cd "$(dirname "$0")/.."

.venv/bin/python -m pytest -q || { echo "단위 시험 실패: 올리지 않음"; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -C . -czf "$TMP/pimon.tgz" pimon deploy/pimon.service pimon.toml.example
scp -q "$TMP/pimon.tgz" "$HOST:/tmp/pimon.tgz"

ssh "$HOST" 'set -eu
python3 -c "import sys; assert sys.version_info >= (3, 11), sys.version" || { echo "python3 3.11 이상 필요"; exit 1; }
rm -rf /tmp/pimon.new && mkdir /tmp/pimon.new && tar -C /tmp/pimon.new -xzf /tmp/pimon.tgz
sudo rm -rf /opt/pimon.prev
[ -d /opt/pimon ] && sudo cp -a /opt/pimon /opt/pimon.prev
sudo mkdir -p /opt/pimon /etc/pimon
sudo rm -rf /opt/pimon/pimon && sudo cp -r /tmp/pimon.new/pimon /opt/pimon/
[ -f /etc/pimon/pimon.toml ] || sudo cp /tmp/pimon.new/pimon.toml.example /etc/pimon/pimon.toml
cd /opt/pimon && sudo python3 -m pimon check-config --config /etc/pimon/pimon.toml
sudo cp /tmp/pimon.new/deploy/pimon.service /etc/systemd/system/pimon.service
sudo systemctl daemon-reload
sudo systemctl enable -q pimon
sudo systemctl restart pimon
sleep 3
systemctl is-active pimon
curl -fsS http://127.0.0.1:8080/status >/dev/null && echo "상태 API 응답 확인"
rm -rf /tmp/pimon.new /tmp/pimon.tgz'
