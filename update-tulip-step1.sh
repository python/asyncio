set -e -x
hg update trollius
hg pull --update
hg update default
hg pull https://code.google.com/p/tulip/
hg update
hg update trollius
hg merge default
echo "Now run ./update-tulip-step2.sh"
