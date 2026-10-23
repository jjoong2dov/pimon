"""가짜 파이 폴더 만들기·값 바꾸기. 파이 없이 PC 에서 pimon 을 돌려 볼 때 쓴다.

  python3 fake/make_fake.py fake/pi                    기본값(정상)으로 만들기
  python3 fake/make_fake.py fake/pi --temp 82.5        온도만 바꾸기
  python3 fake/make_fake.py fake/pi --throttled 0x50005 --link down
"""

import argparse
from pathlib import Path

LED_TRIGGERS = "none rc-feedback kbd-scrolllock timer oneshot heartbeat default-on input panic mmc0 mmc1"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def led_trigger_text(current):
    return " ".join(f"[{t}]" if t == current else t for t in LED_TRIGGERS.split()) + "\n"


def make(root, temp=None, throttled=None, link=None, speed=None, mem_avail_mb=None,
         load=None, uptime=None, fresh=False):
    root = Path(root)
    if fresh or not root.exists():
        temp = 48.3 if temp is None else temp
        throttled = "0x0" if throttled is None else throttled
        link = "up" if link is None else link
        speed = 1000 if speed is None else speed
        mem_avail_mb = 3200 if mem_avail_mb is None else mem_avail_mb
        load = 0.12 if load is None else load
        uptime = 3600 if uptime is None else uptime
        write(root / "sys/class/leds/ACT/trigger", led_trigger_text("mmc0"))
        write(root / "sys/class/leds/ACT/brightness", "0\n")
    if temp is not None:
        write(root / "sys/class/thermal/thermal_zone0/temp", f"{round(temp * 1000)}\n")
    if throttled is not None:
        write(root / "sys/devices/platform/soc/soc:firmware/get_throttled",
              f"{int(throttled, 16):x}\n")
    if link is not None:
        write(root / "sys/class/net/eth0/operstate", f"{link}\n")
        write(root / "sys/class/net/eth0/carrier", "1\n" if link == "up" else "0\n")
    if speed is not None:
        write(root / "sys/class/net/eth0/speed", f"{speed}\n")
    if mem_avail_mb is not None:
        total_kb = 3884 * 1024
        write(root / "proc/meminfo",
              f"MemTotal:       {total_kb} kB\nMemFree:        {mem_avail_mb * 1024 // 2} kB\n"
              f"MemAvailable:   {mem_avail_mb * 1024} kB\n")
    if load is not None or uptime is not None:
        old = (root / "proc/loadavg").read_text().split() if (root / "proc/loadavg").exists() else []
        cur = float(old[0]) if old else 0.0
        load = cur if load is None else load
        write(root / "proc/loadavg", f"{load:.2f} {load:.2f} {load:.2f} 1/180 1234\n")
        if uptime is not None:
            write(root / "proc/uptime", f"{uptime:.2f} {uptime * 3.5:.2f}\n")
    return root


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root")
    p.add_argument("--temp", type=float, help="CPU 온도(도)")
    p.add_argument("--throttled", help="get_throttled 값, 예: 0x0, 0x50005")
    p.add_argument("--link", choices=["up", "down"])
    p.add_argument("--speed", type=int, help="링크 속도(Mb/s)")
    p.add_argument("--mem-avail-mb", type=int, help="남은 메모리(MB, 전체 3884)")
    p.add_argument("--load", type=float)
    p.add_argument("--fresh", action="store_true", help="기본값으로 다시 만들기")
    a = p.parse_args(argv)
    root = make(a.root, a.temp, a.throttled, a.link, a.speed, a.mem_avail_mb, a.load, fresh=a.fresh)
    print(f"가짜 파이: {root}")


if __name__ == "__main__":
    main()
