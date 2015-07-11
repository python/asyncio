set -e

# Check for merge conflicts
if $(git status --porcelain|grep -q '^.U '); then
    echo "Fix the following conflicts:"
    git status
    exit 1
fi

# Ensure that yield from is not used
if $(git diff|grep -q 'yield from'); then
    echo "yield from present in changed code!"
    git diff | grep 'yield from' -B5 -A3
    exit 1
fi

# Ensure that mock patchs trollius module, not asyncio
if $(grep -q 'patch.*asyncio' tests/*.py); then
    echo "Fix following patch lines in tests/"
    grep 'patch.*asyncio' tests/*.py
    exit 1
fi

# Python 2.6 compatibility
if $(grep -q -E '\{[^0-9].*format' */*.py); then
    echo "Issues with Python 2.6 compatibility:"
    grep -E '\{[^0-9].*format' */*.py
    exit 1
fi
if $(grep -q -F 'super()' */*.py); then
    echo "Issues with Python 2.6 compatibility:"
    grep -F 'super()' */*.py
    exit 1
fi

echo "Now run ./update-tulip-step3.sh"
