# Environment for the fxSDK/gint toolchain, without touching shell startup files.
# Usage:  source fxsdk-env.sh
#   ~/.local/bin                    giteapc, fxsdk, sh-elf-gcc, ... (installed by GiteaPC)
#   ~/.local/share/fxsdk-python     Python 3.14 with Pillow, used by fxconv
export PATH="$HOME/.local/share/fxsdk-python/bin:$HOME/.local/bin:$PATH"
