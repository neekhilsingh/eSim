#!/bin/bash
#===============================================================================
#  TEST FIXTURE -- NOT the real FOSSEE install-eSim.sh.
#
#  This file exists only so patch-installer.sh can be exercised in CI and on a
#  machine that has no eSim clone. It reproduces the *shapes* of the upstream
#  constructs that the patcher targets (a pinned KiCad PPA, bare `pip3 install`
#  calls, `python3-distutils` in the apt list, a bare `ghdl` package, an
#  `apt-key add` pipeline, no `set -e`) so that each rewrite can be asserted.
#
#  It is deliberately NOT executed by the test -- only parsed and rewritten.
#  Always run the patcher against your real clone before submitting.
#===============================================================================

kicadPPA="ppa:kicad/kicad-8.0-releases"

function installDependency
{
    echo "Installing dependencies..."
    sudo apt-get update
    sudo apt-get install -y build-essential
    sudo apt-get install -y python3-dev python3-pip python3-distutils
    sudo apt-get install -y python3-pyqt5 libxml2 libxml2-dev libxslt1-dev
    sudo apt-get install -y ngspice
    pip3 install matplotlib
    pip3 install --user tabulate
    python3 -m pip install -r requirements.txt
}

function installKiCad
{
    echo "Installing KiCad..."
    sudo add-apt-repository -y "$kicadPPA"
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends kicad
}

function installNghdl
{
    echo "Installing NGHDL..."
    sudo apt-get install -y ghdl
    wget -qO - https://example.invalid/nghdl.key | sudo apt-key add -
    cd nghdl || return
    ./install-nghdl.sh --install
    cd ..
}

function installNgveri
{
    echo "Installing NgVeri..."
    sudo apt-get install -y verilator
    pip3 install sandpiper-saas
}

function createDesktopStarter
{
    echo "Creating desktop starter..."
    mkdir -p "$HOME/.local/share/applications"
    cp esim.desktop "$HOME/.local/share/applications/"
}

case "$1" in
    --install)
        installDependency
        installKiCad
        installNghdl
        installNgveri
        createDesktopStarter
        echo "eSim installed successfully"
        ;;
    *)
        echo "Usage: $0 --install"
        ;;
esac
