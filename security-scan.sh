#!/usr/bin/env bash
# Runs a battery of security scanners against this repo and writes their
# reports to security-reports/<timestamp>/. Safe to run repeatedly; each run
# gets its own timestamped folder so nothing is overwritten.
#
# Tools used (each installed on first run if missing):
#   - pip-audit : known-vulnerability scan of Python dependencies
#   - bandit    : static analysis for common Python security anti-patterns
#   - semgrep   : flexible static analysis (OWASP Top Ten + Python rulesets)
#   - gitleaks  : scans the full git history for committed secrets/tokens
#
# Also installs a git pre-commit hook (via gitleaks) that blocks new commits
# from introducing secrets, so leaks are caught before they land instead of
# only being found by a later manual scan.
#
# (safety is deliberately not included: its CLI now requires an interactive
# account login/registration, which doesn't fit a non-interactive script.)
#
# NOTE: deliberately no `set -e` here -- these scanners exit non-zero when
# they *find something*, which is a normal, expected outcome, not a script
# failure. Only pipefail is enabled so run_step below can still detect a
# scanner's real exit code through `| tee`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_DIR="$SCRIPT_DIR/security-reports/$TIMESTAMP"
mkdir -p "$REPORT_DIR"

# Python-based scanners live in their own dedicated venv, kept separate from
# the app's own venv/ so scanner dependencies never leak into runtime deps.
TOOLS_VENV="$SCRIPT_DIR/.security-tools-venv"

ISSUES_FOUND=0
SKIPPED=()

echo "======================================================"
echo " HostSpark Security Scan"
echo " Reports: $REPORT_DIR"
echo "======================================================"

echo
echo "Checking prerequisites..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found, installing..."
    sudo apt-get update
    sudo apt-get install -y python3-pip python3-venv
fi

echo "Setting up scanner tools venv (first run only)..."
if [[ ! -x "$TOOLS_VENV/bin/python" ]]; then
    if ! python3 -m venv "$TOOLS_VENV"; then
        # Common cause on a fresh Ubuntu box: the venv module isn't installed
        # yet (python3-venv is a separate package from python3 itself).
        echo "venv creation failed, installing python3-venv and retrying..."
        sudo apt-get update
        sudo apt-get install -y python3-venv
        python3 -m venv "$TOOLS_VENV"
    fi
fi
"$TOOLS_VENV/bin/pip" install --quiet --upgrade pip
"$TOOLS_VENV/bin/pip" install --quiet pip-audit bandit semgrep

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "gitleaks not found, attempting 'sudo apt-get install -y gitleaks'..."
    if ! sudo apt-get install -y gitleaks >/dev/null 2>&1; then
        echo "Could not install gitleaks automatically -- skipping that scan."
        SKIPPED+=("gitleaks")
    fi
fi

echo
echo "Setting up gitleaks pre-commit hook..."
if command -v gitleaks >/dev/null 2>&1 && [[ -d "$SCRIPT_DIR/.git" ]]; then
    HOOK_PATH="$SCRIPT_DIR/.git/hooks/pre-commit"
    cat >"$HOOK_PATH" <<'HOOK_EOF'
#!/usr/bin/env bash
# Installed by security-scan.sh -- blocks commits that would introduce secrets.
# To bypass a known false positive: git commit --no-verify
exec gitleaks protect --staged -v
HOOK_EOF
    chmod +x "$HOOK_PATH"
    echo "Installed at $HOOK_PATH (blocks 'git commit' when staged changes contain secrets)."
else
    echo "Skipped (gitleaks unavailable or this isn't a git checkout)."
fi

# Runs one scanner, streams its output live, saves it to a report file, and
# tracks whether it reported anything (non-zero exit). Relies on `pipefail`
# (set above) so `"$@" | tee` reflects the scanner's own exit code, not tee's.
run_step() {
    local name="$1" outfile="$REPORT_DIR/$2"
    shift 2
    echo
    echo "------------------------------------------------------"
    echo " $name"
    echo "------------------------------------------------------"
    if "$@" 2>&1 | tee "$outfile"; then
        echo "[$name] OK."
    else
        echo "[$name] reported findings (or failed to run) -- see $outfile"
        ISSUES_FOUND=1
    fi
}

run_step "pip-audit (known dependency vulnerabilities)" "pip-audit.txt" \
    "$TOOLS_VENV/bin/pip-audit" -r requirements.lock

run_step "bandit (Python security anti-patterns)" "bandit.txt" \
    "$TOOLS_VENV/bin/bandit" -r "$SCRIPT_DIR" \
    --exclude "$SCRIPT_DIR/venv,$SCRIPT_DIR/tests,$SCRIPT_DIR/.security-tools-venv,$SCRIPT_DIR/.agents" \
    -ll

run_step "semgrep (OWASP Top Ten + Python rulesets)" "semgrep.txt" \
    "$TOOLS_VENV/bin/semgrep" \
    --config p/python --config p/owasp-top-ten \
    --exclude venv --exclude tests --exclude .security-tools-venv --exclude .agents \
    "$SCRIPT_DIR"

if command -v gitleaks >/dev/null 2>&1; then
    run_step "gitleaks (secrets committed to git history)" "gitleaks.txt" \
        gitleaks detect --source "$SCRIPT_DIR" -v \
        --report-path "$REPORT_DIR/gitleaks.json"
fi

echo
echo "======================================================"
if [[ ${#SKIPPED[@]} -gt 0 ]]; then
    echo " Skipped (could not install): ${SKIPPED[*]}"
fi
if [[ "$ISSUES_FOUND" -eq 1 ]]; then
    echo " Scan complete: one or more tools reported findings."
    echo " Review the reports in: $REPORT_DIR"
    echo "======================================================"
    exit 1
fi
echo " Scan complete: no issues reported by any tool."
echo " Reports saved to: $REPORT_DIR"
echo "======================================================"
