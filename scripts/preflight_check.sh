#!/bin/bash
# preflight_check.sh — Run before starting the RCA POC to verify all dependencies.
# Usage: bash scripts/preflight_check.sh

echo "========================================="
echo "  Build RCA Agent — Pre-Flight Check"
echo "========================================="

PASS=0
FAIL=0

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "  [PASS] $label"
        PASS=$((PASS+1))
    else
        echo "  [FAIL] $label"
        FAIL=$((FAIL+1))
    fi
}

echo ""
echo "1. System Requirements"
check "Python 3.11+" "python3 --version | grep -E '3\.(11|12|13|14)'"
check "pip available" "pip3 --version"
check "curl available" "curl --version"
check "unzip available" "unzip -v"

echo ""
echo "2. Python Dependencies"
check "requests library" "python3 -c 'import requests'"
check "pytest available" "python3 -m pytest --version"

echo ""
echo "3. GitHub API Access"
GH_HOST="${GITHUB_API_URL:-https://api.github.com}"
GH_TOKEN="${RCA_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -n "$GH_TOKEN" ]; then
    check "GitHub API reachable" \
        "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: token $GH_TOKEN' $GH_HOST/user | grep -q 200"
    check "Actions API accessible" \
        "curl -s -H 'Authorization: token $GH_TOKEN' '$GH_HOST/user/repos?per_page=1' | grep -q '\['"
else
    echo "  [WARN] No GITHUB_TOKEN or RCA_GITHUB_TOKEN set — skipping GitHub API checks"
    FAIL=$((FAIL+1))
fi

echo ""
echo "4. Ollama Connectivity"
OLLAMA="${OLLAMA_HOST:-http://localhost:11434}"
check "Ollama server reachable" \
    "curl -s -o /dev/null -w '%{http_code}' $OLLAMA/api/tags | grep -q 200"
check "Gemma model loaded" \
    "curl -s $OLLAMA/api/tags | grep -i gemma"
check "Ollama inference works" \
    "curl -s -X POST $OLLAMA/api/chat \
        -H 'Content-Type: application/json' \
        -d '{\"model\":\"gemma3:27b-it-qat\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}]}' \
        | grep -q 'content'"

echo ""
echo "5. Network"
GH_HOST_BARE="${GH_HOST#https://}"
GH_HOST_BARE="${GH_HOST_BARE#http://}"
check "DNS resolves GitHub host" "nslookup $GH_HOST_BARE"
check "Outbound HTTPS works" \
    "curl -s -o /dev/null -w '%{http_code}' https://github.com | grep -qE '(200|301|302)'"

echo ""
echo "6. Project Structure"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
check "src/log_parser.py exists" "test -f '$PROJECT_DIR/src/log_parser.py'"
check "src/ollama_client.py exists" "test -f '$PROJECT_DIR/src/ollama_client.py'"
check "scripts/run_rca.py exists" "test -f '$PROJECT_DIR/scripts/run_rca.py'"
check "requirements.txt exists" "test -f '$PROJECT_DIR/requirements.txt'"

echo ""
echo "========================================="
if [ $FAIL -eq 0 ]; then
    echo "  ALL CHECKS PASSED ($PASS passed, 0 failed)"
    echo "  Ready to run the RCA POC!"
else
    echo "  Results: $PASS passed, $FAIL FAILED"
    echo "  Fix the failures above before proceeding."
fi
echo "========================================="

exit $FAIL
