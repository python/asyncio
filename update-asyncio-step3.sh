set -e -x
./update-asyncio-step2.sh
tox -e py27,py34
git commit -m 'Merge asyncio into trollius'
