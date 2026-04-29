#!/usr/bin/env bash
# HF Range Tracker — Linux / Raspberry Pi installer

set -e
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo
echo -e "${BOLD}================================================${NC}"
echo -e "${BOLD} HF Range Tracker — Linux Installer${NC}"
echo -e "${BOLD}================================================${NC}"
echo

# ── Python ──────────────────────────────────────────────────────────────────
echo -e "${BOLD}[1/4] Checking Python 3...${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}Python 3 not found. Install with: sudo apt install python3 python3-pip${NC}"
    exit 1
fi
python3 --version

# ── pip + packages ──────────────────────────────────────────────────────────
echo
echo -e "${BOLD}[2/4] Installing Python packages...${NC}"
python3 -m pip install --upgrade pip --quiet 2>/dev/null || true
python3 -m pip install -r requirements.txt
echo -e "${GREEN}Done.${NC}"

# ── hamlib ──────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}[3/4] Checking hamlib (rigctld)...${NC}"
if command -v rigctld &>/dev/null; then
    echo -e "${GREEN}rigctld found: $(rigctld --version | head -1)${NC}"
else
    echo -e "${YELLOW}rigctld not found. Installing...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y hamlib-utils
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y hamlib
    elif command -v brew &>/dev/null; then
        brew install hamlib
    else
        echo -e "${RED}Could not auto-install. Install hamlib manually for your distro.${NC}"
        exit 1
    fi
    echo -e "${GREEN}rigctld installed: $(rigctld --version | head -1)${NC}"
fi

# Verify IC-7300 model is supported
if rigctld --list 2>/dev/null | grep -q "7300"; then
    echo -e "${GREEN}IC-7300 (model 3073) supported.${NC}"
else
    echo -e "${YELLOW}Could not verify IC-7300 support — hamlib may be outdated.${NC}"
fi

# ── Output directory ─────────────────────────────────────────────────────────
echo
echo -e "${BOLD}[4/4] Creating output directory...${NC}"
mkdir -p output
echo -e "${GREEN}Done.${NC}"

# ── Find IC-7300 port ────────────────────────────────────────────────────────
echo
echo -e "${BOLD}Looking for IC-7300 serial port...${NC}"
IC7300_PORT=""
for port in /dev/ttyUSB* /dev/ttyACM*; do
    if [ -e "$port" ]; then
        echo -e "  Found serial device: ${GREEN}$port${NC}"
        IC7300_PORT="$port"
    fi
done
if [ -z "$IC7300_PORT" ]; then
    echo -e "${YELLOW}  No USB serial port found. Connect IC-7300 via USB and try again.${NC}"
    IC7300_PORT="/dev/ttyUSB0"
fi

# ── Serial port permissions ──────────────────────────────────────────────────
if [ -n "$IC7300_PORT" ] && [ -e "$IC7300_PORT" ]; then
    if ! groups | grep -qE 'dialout|uucp'; then
        echo
        echo -e "${YELLOW}Adding $USER to dialout group (needed for serial port access)...${NC}"
        sudo usermod -aG dialout "$USER"
        echo -e "${YELLOW}NOTE: Log out and back in for group change to take effect.${NC}"
    fi
fi

# ── Done ────────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}================================================${NC}"
echo -e "${GREEN}${BOLD} Installation complete!${NC}"
echo -e "${BOLD}================================================${NC}"
echo
echo " Next steps:"
echo
echo "  1. On the IC-7300: Menu > SET > Connectors"
echo "     > USB SEND/MOD > set to 'Data'"
echo
echo "  2. Start rigctld (adjust port if needed):"
echo -e "     ${BOLD}rigctld -m 3073 -r ${IC7300_PORT} -s 19200${NC}"
echo
echo "  3. Start the web app:"
echo -e "     ${BOLD}python3 src/web_app.py${NC}"
echo
echo "  4. Open in browser:"
echo -e "     ${BOLD}http://localhost:8000${NC}"
echo
echo "  See docs/setup.md for full details."
echo
