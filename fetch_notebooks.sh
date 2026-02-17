#!/usr/bin/env bash
#
# fetch_notebooks.sh - Fetch and organize lecture notebooks from GitHub repos
#
# Usage: ./fetch_notebooks.sh <list_file> [output_dir]
#
# The list file format (same as list.txt):
#   Lecture NN: <Title>        <- starts a new lecture group
#                              <- blank lines are ignored
#   <Notebook Name>            <- human-readable name (ignored by script)
#   <GitHub URL to .ipynb>     <- the actual notebook URL
#
# Example:
#   Lecture 01: What is NLP?
#
#   Data Preparation
#   https://github.com/user/repo/blob/master/notebooks/data_prep.ipynb
#

set -euo pipefail

LIST_FILE="${1:?Usage: $0 <list_file> [output_dir]}"
OUTPUT_DIR="${2:-.}"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

declare -A CLONED_REPOS  # repo_url -> local_clone_path

current_lecture=""

clone_repo_if_needed() {
    local repo_url="$1"
    if [[ -z "${CLONED_REPOS[$repo_url]+x}" ]]; then
        local repo_name
        repo_name=$(echo "$repo_url" | sed 's|https://github.com/||' | tr '/' '_')
        local clone_path="$TMPDIR/$repo_name"
        echo "  Cloning $repo_url ..."
        git clone --depth 1 "$repo_url" "$clone_path" 2>/dev/null
        CLONED_REPOS[$repo_url]="$clone_path"
    fi
}

copy_supporting_dirs() {
    local clone_path="$1"
    local notebook_dir="$2"
    local common_dir="$OUTPUT_DIR/common"

    # Look for data, images, src, img, assets folders near the notebook
    for support_dir in data images img src assets; do
        if [[ -d "$notebook_dir/$support_dir" ]]; then
            mkdir -p "$common_dir/$support_dir"
            cp -rn "$notebook_dir/$support_dir/"* "$common_dir/$support_dir/" 2>/dev/null || true
        fi
    done
}

echo "=== Notebook Organizer ==="
echo "Reading: $LIST_FILE"
echo "Output:  $OUTPUT_DIR"
echo ""

while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip empty lines
    [[ -z "${line// /}" ]] && continue

    # Check for lecture header
    if [[ "$line" =~ ^Lecture[[:space:]]+([0-9]+) ]]; then
        lecture_num="${BASH_REMATCH[1]}"
        # Remove leading zeros for folder name but keep it simple
        current_lecture="lecture${lecture_num#0}"
        mkdir -p "$OUTPUT_DIR/$current_lecture"
        echo "--- $line ---"
        continue
    fi

    # Check for GitHub URL to .ipynb
    if [[ "$line" =~ ^https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+\.ipynb)$ ]]; then
        repo_slug="${BASH_REMATCH[1]}"
        branch="${BASH_REMATCH[2]}"
        file_path="${BASH_REMATCH[3]}"
        repo_url="https://github.com/$repo_slug"

        if [[ -z "$current_lecture" ]]; then
            echo "WARNING: Found notebook URL before any Lecture header, skipping: $line"
            continue
        fi

        clone_repo_if_needed "$repo_url"

        local_repo="${CLONED_REPOS[$repo_url]}"
        # URL-decode the file path (handle %20 etc.)
        decoded_path=$(printf '%b' "${file_path//%/\\x}")
        source_file="$local_repo/$decoded_path"

        if [[ -f "$source_file" ]]; then
            cp "$source_file" "$OUTPUT_DIR/$current_lecture/"
            echo "  Copied: $(basename "$decoded_path") -> $current_lecture/"
            # Copy supporting directories from the notebook's parent folder
            copy_supporting_dirs "$local_repo" "$(dirname "$source_file")"
        else
            echo "  WARNING: File not found: $decoded_path"
        fi
        continue
    fi

    # Otherwise it's a notebook title line - skip it
done < "$LIST_FILE"

echo ""
echo "=== Done ==="
echo "Notebooks organized in: $OUTPUT_DIR/lecture*/"
echo "Supporting files in:     $OUTPUT_DIR/common/"
