#!/bin/zsh
# oc_probe.sh — 探测 opencode 当前可用模型的存活与速度
#
# 用法:
#   ./oc_probe.sh              # 只探免费模型（-free 后缀），推荐
#   ./oc_probe.sh --all        # 探所有模型（含付费的 xiaomi/*，会产生费用！）
#   ./oc_probe.sh --code       # 用一个小编码任务探（更贴近真实外包场景，较慢）
#
# 输出: 模型 | 耗时 | 结果摘要，按耗时排序。列表变动时请更新 references/models.md

set -u
MODE_ALL=0; MODE_CODE=0
for arg in "$@"; do
  case "$arg" in
    --all)  MODE_ALL=1 ;;
    --code) MODE_CODE=1 ;;
  esac
done

command -v opencode >/dev/null 2>&1 || { echo "❌ 找不到 opencode 命令"; exit 1; }

WORK=$(mktemp -d /tmp/oc_probe.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

echo "→ 拉取模型列表..."
ALL_MODELS=("${(@f)$(opencode models 2>/dev/null)}")
[[ ${#ALL_MODELS[@]} -eq 0 ]] && { echo "❌ opencode models 无输出，检查网络/安装"; exit 1; }

MODELS=()
for m in $ALL_MODELS; do
  [[ -z "$m" ]] && continue
  if (( MODE_ALL )); then
    MODELS+=("$m")
  elif [[ "$m" == *-free ]]; then
    MODELS+=("$m")
  fi
done
(( MODE_ALL )) && echo "⚠️  --all 模式会调用付费模型，可能产生费用"
echo "→ 待探测 ${#MODELS[@]} 个模型，并行执行...\n"

if (( MODE_CODE )); then
  PROMPT="Create a file probe.txt in the current directory containing exactly: OK. Then stop."
else
  PROMPT="Reply with exactly one word: PONG"
fi

RESULTS="$WORK/results"
probe_one() {
  local m="$1"
  local sub="$WORK/${m//\//_}"
  mkdir -p "$sub"
  local start=$(date +%s)
  local out
  out=$(cd "$sub" && opencode run --dir "$sub" -m "$m" "$PROMPT" 2>&1 | tr '\n' ' ' | sed 's/  */ /g')
  local rc=$?
  local dur=$(( $(date +%s) - start ))
  local verdict="?"
  if (( MODE_CODE )); then
    [[ -f "$sub/probe.txt" ]] && verdict="✅ 写文件成功" || verdict="❌ 未写出文件"
  else
    [[ "$out" == *PONG* ]] && verdict="✅ 存活" || verdict="❌ 异常"
  fi
  printf '%s\t%s\t%s\t%s\n' "$dur" "$m" "$verdict" "${out: -90}" >> "$RESULTS"
}

for m in $MODELS; do probe_one "$m" & done
wait

echo "耗时  模型                                      状态          摘要"
echo "----------------------------------------------------------------------------------"
sort -n "$RESULTS" 2>/dev/null | while IFS=$'\t' read -r dur m verdict out; do
  printf '%4ss  %-40s  %-12s  %s\n' "$dur" "$m" "$verdict" "$out"
done
echo "\n提示：列表或存活状态有变 → 更新 references/models.md 并在 experience.md 记一笔（带日期）。"
