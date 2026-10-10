#!/bin/bash
# Runs as root on the target. Prints a tar.gz of results to stdout.
# BENCH=1 adds load benchmarks, IDLE_WAIT=seconds to settle before sampling.
set -u
export LC_ALL=C
OUT=$(mktemp -d /tmp/gluebench.XXXXXX)
BENCH=${BENCH:-0}
IDLE_WAIT=${IDLE_WAIT:-30}
SAMPLE=${SAMPLE:-30}
have() { command -v "$1" >/dev/null 2>&1; }
kv() { printf '%s=%s\n' "$1" "$2" >>"$OUT/summary.txt"; }
cap() { local f=$1; shift; "$@" >"$OUT/$f" 2>&1 || true; }

# meta
. /etc/os-release
kv os "$PRETTY_NAME"
kv kernel "$(uname -r)"
kv date "$(date -Is)"
kv cpu "$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //')"
kv nproc "$(nproc)"
init=$(ps -p 1 -o comm=)
kv init "$init"

# boot: seconds since boot when greeter / compositor started
up0=$(cut -d' ' -f1 /proc/uptime)
for p in greetd sddm gdm lightdm ly-dm ly gluewc niri sway Hyprland labwc kwin_wayland gnome-shell cinnamon Xorg; do
	pid=$(pgrep -o -x "$p") || continue
	et=$(ps -p "$pid" -o etimes= | tr -d ' ')
	kv "boot_to_$p" "$(awk -v u="$up0" -v e="$et" 'BEGIN{printf "%.1f", u-e}')"
done
have systemd-analyze && cap systemd-analyze.txt systemd-analyze
have systemd-analyze && cap systemd-blame.txt systemd-analyze blame
kv uptime_s "$up0"
cap dmesg.txt dmesg

# config snapshot
cap cmdline.txt cat /proc/cmdline
cap sysctl.txt sysctl -a
cap lsmod.txt sh -c 'lsmod | sort'
cap lspci.txt lspci -nnk
kv modules "$(lsmod | tail -n +2 | wc -l)"
cpu0=/sys/devices/system/cpu/cpu0/cpufreq
kv governor "$(cat $cpu0/scaling_governor 2>/dev/null)"
kv cpufreq_driver "$(cat $cpu0/scaling_driver 2>/dev/null)"
kv epp "$(cat $cpu0/energy_performance_preference 2>/dev/null)"
kv freq_min_max_khz "$(cat $cpu0/scaling_min_freq $cpu0/scaling_max_freq 2>/dev/null | tr '\n' ' ')"
kv amd_pstate "$(cat /sys/devices/system/cpu/amd_pstate/status 2>/dev/null)"
kv intel_pstate "$(cat /sys/devices/system/cpu/intel_pstate/status 2>/dev/null)"
kv no_turbo "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null)"
kv boost "$(cat /sys/devices/system/cpu/cpufreq/boost 2>/dev/null)"
kv platform_profile "$(cat /sys/firmware/acpi/platform_profile 2>/dev/null)"
kv sched_ext "$(cat /sys/kernel/sched_ext/state 2>/dev/null) $(cat /sys/kernel/sched_ext/root/ops 2>/dev/null)"
kv thp "$(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null)"
kv thp_defrag "$(cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null)"
kv swappiness "$(cat /proc/sys/vm/swappiness)"
cap swap.txt cat /proc/swaps
have zramctl && cap zram.txt zramctl
for d in /sys/block/*/queue/scheduler; do
	n=$(echo "$d" | cut -d/ -f4)
	case $n in loop*|ram*|zram*) continue ;; esac
	kv "iosched_$n" "$(cat "$d")"
done
cap mounts.txt findmnt -rno TARGET,FSTYPE,OPTIONS -t ext4,btrfs,xfs,f2fs,vfat

# services
case $init in
systemd) cap services.txt systemctl list-units --type=service --state=running --no-legend --plain ;;
runit*) cap services.txt sh -c 'for s in /run/runit/service/* /var/service/*; do [ -e "$s" ] && sv status "$s"; done' ;;
dinit) cap services.txt dinitctl list ;;
*) have rc-status && cap services.txt rc-status -a ;;
esac
for s in ananicy-cpp irqbalance thermald power-profiles-daemon tuned auto-cpufreq tlp scx_loader scx earlyoom systemd-oomd; do
	pgrep -x "${s:0:15}" >/dev/null && kv "running_$s" yes
done

# packages
if have pacman; then
	kv packages "$(pacman -Q | wc -l)"
	cap pkgs.txt pacman -Q
	cap repos.txt sh -c "grep -E '^\[|^Include|^Server' /etc/pacman.conf"
	kv arch_level "$(grep -m1 -E '^Architecture' /etc/pacman.conf | cut -d= -f2- | xargs)"
	cap makepkg-flags.txt grep -E '^(CFLAGS|CXXFLAGS|LDFLAGS|RUSTFLAGS|MAKEFLAGS)=' /etc/makepkg.conf
fi
/lib/ld-linux-x86-64.so.2 --help 2>/dev/null | grep -E 'x86-64-v[234]' >"$OUT/hwcaps.txt"

# idle sampling
sleep "$IDLE_WAIT"
read_stat() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6} /^ctxt/{print $2}' /proc/stat | tr '\n' ' '; }
irq_total() { awk 'NR>1{for(i=2;i<=NF;i++) if($i~/^[0-9]+$/) s+=$i} END{print s}' /proc/interrupts; }
bat=$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | head -1)
pw() {
	[ -n "$bat" ] || return
	if [ -r "$bat/power_now" ]; then cat "$bat/power_now"
	else awk -v c="$(cat "$bat/current_now" 2>/dev/null)" -v v="$(cat "$bat/voltage_now" 2>/dev/null)" 'BEGIN{printf "%d", c*v/1e6}'; fi
}
[ -n "$bat" ] && kv battery_status "$(cat "$bat/status")"
set -- $(read_stat); t0=$1 i0=$2 c0=$3
q0=$(irq_total)
psum=0 pn=0 fsum=0 fn=0
for _ in $(seq "$SAMPLE"); do
	sleep 1
	p=$(pw); [ -n "$p" ] && { psum=$((psum + p)); pn=$((pn + 1)); }
	for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do fsum=$((fsum + $(cat "$f"))); fn=$((fn + 1)); done
done
set -- $(read_stat); t1=$1 i1=$2 c1=$3
q1=$(irq_total)
kv idle_cpu_busy_pct "$(awk -v a=$((t1 - t0)) -v b=$((i1 - i0)) 'BEGIN{printf "%.2f", 100*(a-b)/a}')"
kv idle_ctxsw_per_s "$(( (c1 - c0) / SAMPLE ))"
kv idle_irq_per_s "$(( (q1 - q0) / SAMPLE ))"
[ "$pn" -gt 0 ] && kv idle_power_w "$(awk -v s=$psum -v n=$pn 'BEGIN{printf "%.2f", s/n/1e6}')"
[ "$fn" -gt 0 ] && kv idle_avg_freq_mhz "$((fsum / fn / 1000))"
kv mem_used_mb "$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{print int((t-a)/1024)}' /proc/meminfo)"
kv mem_total_mb "$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)"
kv processes "$(ps -e --no-headers | wc -l)"
kv threads "$(ps -eL --no-headers | wc -l)"
kv kthreads "$(ps -e -o ppid= | grep -cx ' *2')"
cap top-rss.txt sh -c 'ps -eo rss,comm --sort=-rss | head -30'
cap meminfo.txt cat /proc/meminfo

# load benchmarks, 3 runs each, median
med() { sort -n | awk '{a[NR]=$1} END{print a[int((NR+1)/2)]}'; }
tm() { local s=$(date +%s.%N); "$@" >/dev/null 2>&1; awk -v s="$s" -v e="$(date +%s.%N)" 'BEGIN{printf "%.3f\n", e-s}'; }
if [ "$BENCH" = 1 ]; then
	W=$(mktemp -d /var/tmp/gluebench.XXXXXX)
	seq 1 20000000 >"$W/data.txt"
	sync; echo 3 >/proc/sys/vm/drop_caches
	have zstd && kv bench_zstd19_mt_s "$(for _ in 1 2 3; do tm zstd -19 -T0 -f "$W/data.txt" -o "$W/o.zst"; done | med)"
	have xz && kv bench_xz6_1t_s "$(for _ in 1 2 3; do tm xz -6 -T1 -kfc "$W/data.txt"; done | med)"
	kv bench_sha256_s "$(for _ in 1 2 3; do tm sha256sum "$W/data.txt"; done | med)"
	have python3 && kv bench_python_s "$(for _ in 1 2 3; do tm python3 -c 'sum(i*i for i in range(30000000))'; done | med)"
	# random data so btrfs compression can't skew it
	head -c 512M /dev/urandom >/dev/shm/gluebench.rnd
	kv bench_disk_write_s "$(for _ in 1 2 3; do tm dd if=/dev/shm/gluebench.rnd of="$W/dd" bs=1M oflag=direct conv=fsync; done | med)"
	rm -f /dev/shm/gluebench.rnd
	sync; echo 3 >/proc/sys/vm/drop_caches
	kv bench_disk_read_s "$(for _ in 1 2 3; do echo 3 >/proc/sys/vm/drop_caches; tm dd if="$W/dd" of=/dev/null bs=1M iflag=direct; done | med)"
	if have cyclictest; then
		cyclictest -q -m -S -p 90 -i 1000 -D 30 >"$OUT/cyclictest.txt" 2>&1
		kv cyclictest_max_us "$(awk '/Max:/{for(i=1;i<=NF;i++) if($i=="Max:") print $(i+1)}' "$OUT/cyclictest.txt" | sort -n | tail -1)"
		kv cyclictest_avg_us "$(awk '/Avg:/{for(i=1;i<=NF;i++) if($i=="Avg:") {s+=$(i+1); n++}} END{if(n) printf "%.0f", s/n}' "$OUT/cyclictest.txt")"
	else
		kv cyclictest_max_us "missing (rt-tests)"
	fi
	rm -rf "$W"
fi

tar -C "$OUT" -czf - .
rm -rf "$OUT"
