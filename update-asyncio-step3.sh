set -e -x
./update-asyncio-step2.sh
tox -e py27,py34

git status
echo
echo "Now type:"
echo "git commit -m 'Merge asyncio into trollius'"
echo
echo "You may have to add unstaged files"
