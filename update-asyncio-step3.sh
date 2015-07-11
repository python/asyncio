set -e -x
./update-tulip-step2.sh
tox -e py27,py34
hg ci -m 'Merge Tulip into Trollius'
