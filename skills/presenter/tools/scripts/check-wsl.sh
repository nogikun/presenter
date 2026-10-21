#!/usr/bin/env bash
# Read-only checks. Run this inside the intended WSL distribution.
set -u
failed=0
section() { printf '\n%s\n' "$1"; }
section 'Kernel / distribution'
uname -r
if ! grep -qi microsoft /proc/sys/kernel/osrelease; then
    printf 'ERROR: run this check inside WSL 2.\n'
    exit 2
fi
cat /etc/os-release
section 'System RAM and swap (not GPU VRAM)'
free -h
section 'GPU / Windows-provided CUDA driver'
gpu_tool=$(command -v nvidia-smi || true)
if [ -z "$gpu_tool" ] && [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
    gpu_tool=/usr/lib/wsl/lib/nvidia-smi
fi
if [ -n "$gpu_tool" ]; then
    "$gpu_tool" || failed=1
else
    printf 'ERROR: Windows GPU integration is unavailable. Check WSL 2 and the Windows driver.\n'
    failed=1
fi
section 'Linux CUDA compiler (separate from the Windows toolkit)'
cuda_compiler=$(command -v nvcc || true)
if [ -n "$cuda_compiler" ] && [ "$cuda_compiler" != "${cuda_compiler%.exe}" ]; then
    printf 'ERROR: PATH selected a Windows nvcc.exe; install/select a Linux toolkit.\n'
    failed=1
elif [ -n "$cuda_compiler" ]; then
    printf '%s\n' "$cuda_compiler"
    "$cuda_compiler" --version || failed=1
else
    printf 'ERROR: Linux nvcc not found. For s2.cpp use CUDA Toolkit >=12.4, without a Linux NVIDIA driver.\n'
    failed=1
fi
section 'Build tools'
for tool in git cmake c++ python3; do
    command -v "$tool" || failed=1
done
section 'DNS'
for host in github.com huggingface.co; do
    getent ahosts "$host" || failed=1
done
section 'Current filesystem'
df -h .
case "$PWD" in
    /mnt/*) printf 'For Linux builds and model storage, prefer a directory under Linux $HOME.\n' ;;
esac
printf '\nRead-only check finished. Exit code: %s\n' "$failed"
exit "$failed"
