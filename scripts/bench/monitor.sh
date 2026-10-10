#!/bin/bash
# Runs as the user on the target, prints one CSV line per second until killed.
export LC_ALL=C
hw() { for h in /sys/class/hwmon/*; do [ "$(cat "$h/name")" = "$1" ] && { echo "$h"; return; }; done; }
core=$(hw coretemp)
fanf=$(ls "$(hw asus)"/fan*_input 2>/dev/null | head -1)
bat=$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | head -1)
gpu=0; command -v nvidia-smi >/dev/null && gpu=1

stat() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat; }
read -r t0 i0 < <(stat)
echo "time,cpu_pct,freq_avg_mhz,freq_max_mhz,cpu_temp_c,fan_rpm,mem_used_mb,swap_used_mb,bat_w,gpu_pct,gpu_mhz,gpu_w,gpu_temp_c,gpu_mem_mb,top_proc,top_proc_pct"
while sleep 1; do
	read -r t1 i1 < <(stat)
	cpu=$(awk -v a=$((t1 - t0)) -v b=$((i1 - i0)) 'BEGIN{printf "%.1f", a ? 100*(a-b)/a : 0}')
	t0=$t1 i0=$i1
	fr=$(cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq | awk '{s+=$1; if($1>m)m=$1} END{printf "%d,%d", s/NR/1000, m/1000}')
	ct=$([ -n "$core" ] && awk '{printf "%.0f", $1/1000}' "$core/temp1_input")
	fan=$([ -n "$fanf" ] && cat "$fanf")
	mem=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} /SwapTotal/{st=$2} /SwapFree/{sf=$2} END{printf "%d,%d", (t-a)/1024, (st-sf)/1024}' /proc/meminfo)
	bw=$([ -n "$bat" ] && [ "$(cat "$bat/status")" = Discharging ] && awk '{printf "%.1f", $1/1e6}' "$bat/power_now" 2>/dev/null)
	g=",,,,"
	[ $gpu = 1 ] && g=$(nvidia-smi --query-gpu=utilization.gpu,clocks.gr,power.draw,temperature.gpu,memory.used --format=csv,noheader,nounits | tr -d ' ')
	top=$(ps -eo comm,pcpu --sort=-pcpu --no-headers | head -1 | awk '{printf "%s,%s", $1, $2}')
	echo "$(date +%T),$cpu,$fr,$ct,$fan,$mem,$bw,$g,$top"
done
