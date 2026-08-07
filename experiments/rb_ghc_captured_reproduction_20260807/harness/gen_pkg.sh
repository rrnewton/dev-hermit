#!/bin/bash
# Generate a multi-module Haskell package with a wide dependency graph so that
# `ghc --make -jN` genuinely compiles many modules concurrently. A single
# top module (Main) depends on all leaf modules; leaves are independent so the
# scheduler is free to interleave them across worker threads.
set -euo pipefail
DEST="${1:-/work/pkg}"
NLEAF="${2:-40}"
rm -rf "$DEST"
mkdir -p "$DEST/src"
cd "$DEST/src"

# Leaf modules: each exports a handful of class instances, type families and
# top-level bindings. Ordering/unique-allocation sensitive constructs
# (type-class instance selection, record fields, derived instances) are the
# historical sources of GHC parallel ABI-hash nondeterminism.
for i in $(seq 1 "$NLEAF"); do
  cat > "Leaf$i.hs" <<EOF
{-# LANGUAGE DeriveGeneric #-}
module Leaf$i (Rec$i(..), classify$i, table$i) where
import qualified Data.Map as M
import Data.List (sortBy, foldl')
import Data.Ord (comparing)

data Rec$i = Rec$i { fa$i :: !Int, fb$i :: !String, fc$i :: ![Int] }
  deriving (Eq, Ord, Show)

classify$i :: Int -> String
classify$i n
  | n \`mod\` 7 == 0 = "seven"
  | n \`mod\` 3 == 0 = "three"
  | even n         = "even"
  | otherwise      = "odd"

table$i :: M.Map Int String
table$i = M.fromList [ (k, classify$i (k * $i)) | k <- [1..64] ]
EOF
done

# Aggregator modules pull leaves together in groups to add a middle graph layer.
G=8
grp=0
for start in $(seq 1 "$G" "$NLEAF"); do
  grp=$((grp+1))
  end=$((start+G-1)); [ "$end" -gt "$NLEAF" ] && end="$NLEAF"
  imports=""; body=""
  for j in $(seq "$start" "$end"); do
    imports="$imports\nimport qualified Leaf$j"
    body="$body\n    , M.size Leaf$j.table$j"
  done
  {
    echo "module Group$grp (sizes$grp) where"
    echo "import qualified Data.Map as M"
    echo -e "$imports"
    echo "sizes$grp :: [Int]"
    echo -e "sizes$grp = [ 0$body ]"
  } > "Group$grp.hs"
done

# Main depends on all groups.
{
  echo "module Main (main) where"
  for g in $(seq 1 "$grp"); do echo "import qualified Group$g"; done
  echo "main :: IO ()"
  echo -n "main = print (sum (concat ["
  sep=""
  for g in $(seq 1 "$grp"); do echo -n "${sep}Group$g.sizes$g"; sep=", "; done
  echo "]))"
} > "Main.hs"

echo "Generated $NLEAF leaves + $grp groups + Main in $DEST/src"
ls "$DEST/src" | wc -l
