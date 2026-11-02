#!/usr/bin/env bash
set -euo pipefail

check_only=false
revision=main
source_dir=
python_executable=
while (($#)); do
    case "$1" in
        --check-only) check_only=true ;;
        --revision) revision=${2:?--revision needs a value}; shift ;;
        --source-dir) source_dir=${2:?--source-dir needs a value}; shift ;;
        --python) python_executable=${2:?--python needs a value}; shift ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

[[ $(uname -s) == Darwin ]] || { printf 'setup-s2.sh supports macOS only.\n' >&2; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
home_root=${PRESENTER_TTS_HOME:-"$HOME/.local/share/presenter-tts"}
runtime_root="$home_root/cache"

for tool in git uv xcrun patch; do
    command -v "$tool" >/dev/null || { printf '%s is required.\n' "$tool" >&2; exit 2; }
done
xcrun --find clang++ >/dev/null
xcrun --find metal >/dev/null

if command -v cmake >/dev/null; then
    cmake=(cmake)
else
    cmake=(uvx --from cmake cmake)
fi
"${cmake[@]}" --version >/dev/null

if $check_only; then
    printf '{"platform":"macos","accelerator":"metal","cmake":"%s","ready_to_configure":true}\n' "${cmake[*]}"
    exit 0
fi

mkdir -p "$runtime_root"
if [[ -z $python_executable ]]; then
    python_executable="$repo_root/.venv/bin/python"
    if [[ ! -x $python_executable ]]; then
        uv sync --project "$repo_root"
    fi
fi
[[ -x $python_executable ]] || { printf 'Python is missing: %s\n' "$python_executable" >&2; exit 2; }

if [[ -z $source_dir ]]; then
    source_dir="$runtime_root/s2.cpp"
    if [[ ! -d $source_dir ]]; then
        git clone --recurse-submodules https://github.com/rodrigomatta/s2.cpp.git "$source_dir"
        git -C "$source_dir" checkout "$revision"
        git -C "$source_dir" submodule update --init --recursive
    fi
fi
source_dir=$(cd -- "$source_dir" && pwd)
build_dir="$runtime_root/s2-build"
"${cmake[@]}" -S "$source_dir" -B "$build_dir" -DCMAKE_BUILD_TYPE=Release \
    -DS2_METAL=ON -DS2_BUILD_SHARED_LIBRARIES=OFF
"${cmake[@]}" --build "$build_dir" --parallel "$(sysctl -n hw.ncpu)"
[[ -x "$build_dir/s2" ]] || { printf 's2.cpp build did not produce %s\n' "$build_dir/s2" >&2; exit 2; }

"$python_executable" "$script_dir/fetch_s2.py" --directory "$runtime_root/models"
git -C "$source_dir" rev-parse HEAD > "$runtime_root/s2-source-revision.txt"
# Build the Core Audio playback helper now, so the first `say` does not wait for swiftc.
"$python_executable" -c 'from presenter_tts.audio import mac_pcm_player; mac_pcm_player()' \
    || printf 'Warning: the macOS playback helper did not build; --play will use ffplay.\n' >&2
printf 'Setup finished. Run: uv run presenter-tts server start\n'
