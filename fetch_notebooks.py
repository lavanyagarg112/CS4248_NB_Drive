#!/usr/bin/env python3
"""
fetch_notebooks.py - Complete pipeline to fetch, organize, and prepare lecture notebooks.

Usage: python fetch_notebooks.py list.txt [output_dir]

Pipeline:
  1. Parse list.txt → structured lecture/notebook list
  2. Clone repos (shallow, deduplicated) into temp dir
  3. Clean stale .ipynb files & copy new/changed notebooks
  4. Merge assets (images/, src/, data/) into common/ (skip already-merged repos)
  5. Fix paths in notebooks (../common/ prefixes, sys.path.insert)
  6. Generate index.html (skip if unchanged)
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote


# ── Step 1: Parse list.txt ──────────────────────────────────────────────────

def parse_list(list_path: str) -> list[dict]:
    """Parse list.txt into structured lecture data.

    Returns list of:
      {lecture_num: int, lecture_title: str, notebooks: [
        {name: str, optional: bool, github_url: str, repo: str, branch: str, file_path: str, filename: str}
      ]}
    """
    lectures = []
    current = None

    with open(list_path) as f:
        lines = f.read().splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line:
            continue

        # Lecture header: "Lecture 01: Title"
        m = re.match(r'^Lecture\s+(\d+):\s*(.+)$', line)
        if m:
            current = {
                'lecture_num': int(m.group(1)),
                'lecture_title': m.group(2).strip(),
                'notebooks': [],
            }
            lectures.append(current)
            continue

        # Must be a notebook title line; next non-blank line should be a URL
        if current is None:
            continue

        title_line = line
        # Find the URL line
        url_line = None
        while i < len(lines):
            candidate = lines[i].strip()
            i += 1
            if candidate:
                url_line = candidate
                break

        if not url_line or not url_line.startswith('https://github.com/'):
            continue

        # Parse optional flag and clean display name
        optional = False
        display_name = title_line
        opt_match = re.search(r'\s*\(optional(?:;\s*(.+?))?\)\s*$', title_line, re.IGNORECASE)
        if opt_match:
            optional = True
            # Strip the (optional...) from display name
            display_name = title_line[:opt_match.start()].strip()
            # Keep meaningful suffixes like "implementation from scratch" → "(from scratch)"
            desc = opt_match.group(1) or ''
            if 'from scratch' in desc.lower():
                display_name += ' (from scratch)'

        # Parse GitHub URL
        gm = re.match(
            r'^https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+\.ipynb)$',
            url_line,
        )
        if not gm:
            print(f'  WARNING: Could not parse URL: {url_line}')
            continue

        repo = f'https://github.com/{gm.group(1)}'
        branch = gm.group(2)
        file_path = unquote(gm.group(3))
        filename = os.path.basename(file_path)

        current['notebooks'].append({
            'name': display_name,
            'optional': optional,
            'github_url': url_line,
            'repo': repo,
            'branch': branch,
            'file_path': file_path,
            'filename': filename,
        })

    return lectures


# ── Step 2: Clone repos ─────────────────────────────────────────────────────

def clone_repos(lectures: list[dict], tmp_dir: str) -> dict[str, str]:
    """Shallow-clone each unique repo. Returns {repo_url: local_path}."""
    repos = {}
    for lec in lectures:
        for nb in lec['notebooks']:
            repo = nb['repo']
            if repo not in repos:
                slug = repo.replace('https://github.com/', '').replace('/', '_')
                dest = os.path.join(tmp_dir, slug)
                print(f'  Cloning {repo} ...')
                subprocess.run(
                    ['git', 'clone', '--depth', '1', repo, dest],
                    capture_output=True,
                )
                repos[repo] = dest
    return repos


# ── Step 3: Clean & copy notebooks ──────────────────────────────────────────

def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def clean_and_copy(lectures: list[dict], repos: dict[str, str], output_dir: str):
    """Remove stale .ipynb files and copy new/changed notebooks."""
    # Build set of expected files per lecture folder
    expected: dict[str, set[str]] = {}
    for lec in lectures:
        folder = f'Lecture{lec["lecture_num"]}'
        expected.setdefault(folder, set())
        for nb in lec['notebooks']:
            expected[folder].add(nb['filename'])

    # Clean stale notebooks
    for folder, filenames in expected.items():
        folder_path = os.path.join(output_dir, folder)
        if os.path.isdir(folder_path):
            for existing in os.listdir(folder_path):
                if existing.endswith('.ipynb') and existing not in filenames:
                    stale = os.path.join(folder_path, existing)
                    os.remove(stale)
                    print(f'  Removed stale: {folder}/{existing}')

    # Copy notebooks
    for lec in lectures:
        folder = f'Lecture{lec["lecture_num"]}'
        folder_path = os.path.join(output_dir, folder)
        os.makedirs(folder_path, exist_ok=True)

        for nb in lec['notebooks']:
            clone_path = repos[nb['repo']]
            src = os.path.join(clone_path, nb['file_path'])
            dst = os.path.join(folder_path, nb['filename'])

            if not os.path.isfile(src):
                print(f'  WARNING: File not found: {nb["file_path"]}')
                continue

            if os.path.isfile(dst) and file_hash(src) == file_hash(dst):
                print(f'  Skipped (unchanged): {folder}/{nb["filename"]}')
                continue

            shutil.copy2(src, dst)
            print(f'  Copied: {folder}/{nb["filename"]}')


# ── Step 4: Merge assets into common/ ────────────────────────────────────────

ASSET_DIRS = ('images', 'src', 'data')


def merge_assets(lectures: list[dict], repos: dict[str, str], output_dir: str):
    """Copy asset dirs (images/, src/, data/) into common/, tracking merged repos."""
    common_dir = os.path.join(output_dir, 'common')
    os.makedirs(common_dir, exist_ok=True)
    repos_file = os.path.join(common_dir, '.repos')

    # Load already-merged repos
    merged = set()
    if os.path.isfile(repos_file):
        with open(repos_file) as f:
            merged = {line.strip() for line in f if line.strip()}

    # Collect notebook parent dirs per repo
    repo_notebook_dirs: dict[str, set[str]] = {}
    for lec in lectures:
        for nb in lec['notebooks']:
            repo = nb['repo']
            clone_path = repos[repo]
            nb_parent = os.path.dirname(os.path.join(clone_path, nb['file_path']))
            repo_notebook_dirs.setdefault(repo, set()).add(nb_parent)

    new_merged = []
    for repo_url, clone_path in repos.items():
        if repo_url in merged:
            print(f'  Assets already merged: {repo_url}')
            continue

        # Directories to search: repo root + each notebook parent dir
        search_dirs = {clone_path}
        if repo_url in repo_notebook_dirs:
            search_dirs |= repo_notebook_dirs[repo_url]

        found_any = False
        for search_dir in search_dirs:
            for asset in ASSET_DIRS:
                asset_src = os.path.join(search_dir, asset)
                if os.path.isdir(asset_src):
                    asset_dst = os.path.join(common_dir, asset)
                    os.makedirs(asset_dst, exist_ok=True)
                    _copy_tree_no_clobber(asset_src, asset_dst)
                    found_any = True

        if found_any:
            print(f'  Merged assets from: {repo_url}')
        new_merged.append(repo_url)

    # Update .repos
    if new_merged:
        with open(repos_file, 'a') as f:
            for url in new_merged:
                f.write(url + '\n')


def _copy_tree_no_clobber(src_dir: str, dst_dir: str):
    """Recursively copy files from src_dir to dst_dir without overwriting."""
    for root, dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        dst_root = os.path.join(dst_dir, rel) if rel != '.' else dst_dir
        os.makedirs(dst_root, exist_ok=True)
        for fname in files:
            dst_file = os.path.join(dst_root, fname)
            if not os.path.exists(dst_file):
                shutil.copy2(os.path.join(root, fname), dst_file)


# ── Step 5: Fix paths in notebooks ──────────────────────────────────────────

def fix_notebook_paths(lectures: list[dict], output_dir: str):
    """Rewrite paths in notebooks to use ../common/ and add sys.path.insert."""
    for lec in lectures:
        folder = f'Lecture{lec["lecture_num"]}'
        for nb in lec['notebooks']:
            path = os.path.join(output_dir, folder, nb['filename'])
            if not os.path.isfile(path):
                continue
            _fix_single_notebook(path, folder, nb['filename'])


def _fix_single_notebook(path: str, folder: str, filename: str):
    with open(path, 'r') as f:
        nb = json.load(f)

    changed = False

    for cell in nb.get('cells', []):
        cell_type = cell.get('cell_type', '')
        source = cell.get('source', [])
        if not source:
            continue

        new_source = list(source)

        if cell_type == 'markdown':
            new_source = _fix_markdown_paths(new_source)
        elif cell_type == 'code':
            new_source = _fix_code_paths(new_source)

        if new_source != source:
            cell['source'] = new_source
            changed = True

    # Add sys.path.insert if needed
    if _needs_sys_path(nb) and not _has_sys_path(nb):
        _add_sys_path(nb)
        changed = True

    if changed:
        with open(path, 'w') as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
            f.write('\n')
        print(f'  Fixed paths: {folder}/{filename}')
    else:
        print(f'  Paths OK: {folder}/{filename}')


def _fix_markdown_paths(source: list[str]) -> list[str]:
    """Fix image/data paths in markdown cells."""
    result = []
    for line in source:
        # data/images/ → ../common/data/images/ (must come BEFORE images/ rule)
        line = re.sub(r'(?<!\.\./common/)data/images/', '../common/data/images/', line)
        # images/ → ../common/images/ (but not if already ../common/ or data/../common/)
        line = re.sub(r'(?<!\.\./common/)(?<!common/data/)images/', '../common/images/', line)
        result.append(line)
    return result


def _fix_code_paths(source: list[str]) -> list[str]:
    """Fix data paths in code cells."""
    result = []
    for line in source:
        # Skip download_dataset() lines
        if 'download_dataset' in line:
            result.append(line)
            continue
        # data/corpora/ data/grammars/ data/datasets/ → ../common/data/...
        for subdir in ('corpora', 'grammars', 'datasets'):
            pattern = rf'(?<!\.\./)(?<!common/)data/{subdir}/'
            replacement = f'../common/data/{subdir}/'
            line = re.sub(pattern, replacement, line)
        result.append(line)
    return result


def _needs_sys_path(nb: dict) -> bool:
    """Check if any code cell has 'from src.' imports."""
    for cell in nb.get('cells', []):
        if cell.get('cell_type') != 'code':
            continue
        for line in cell.get('source', []):
            if re.match(r'\s*(from\s+src\.|import\s+src\.)', line):
                return True
    return False


def _has_sys_path(nb: dict) -> bool:
    """Check if sys.path.insert for ../common already exists."""
    for cell in nb.get('cells', []):
        if cell.get('cell_type') != 'code':
            continue
        text = ''.join(cell.get('source', []))
        if '../common' in text and 'sys.path' in text:
            return True
    return False


def _add_sys_path(nb: dict):
    """Insert sys.path.insert(0, '../common') before the first cell with 'from src.'."""
    for i, cell in enumerate(nb.get('cells', [])):
        if cell.get('cell_type') != 'code':
            continue
        for line in cell.get('source', []):
            if re.match(r'\s*(from\s+src\.|import\s+src\.)', line):
                # Prepend to this cell's source
                cell['source'] = [
                    'import sys\n',
                    "sys.path.insert(0, '../common')\n",
                    '\n',
                ] + cell['source']
                return


# ── Step 6: Generate index.html ─────────────────────────────────────────────

def generate_index(lectures: list[dict], output_dir: str):
    """Generate index.html from the lecture list. Skip if unchanged."""
    html = _build_html(lectures)
    index_path = os.path.join(output_dir, 'index.html')

    if os.path.isfile(index_path):
        with open(index_path) as f:
            existing = f.read()
        if existing == html:
            print('  index.html unchanged, skipped.')
            return

    with open(index_path, 'w') as f:
        f.write(html)
    print('  Generated index.html')


def _build_html(lectures: list[dict]) -> str:
    parts = []
    parts.append("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CS4248 Notebooks</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f5f7fa; color: #1a1a2e; padding: 2rem; max-width: 860px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin-bottom: 0.3rem; }
  .subtitle { color: #666; font-size: 0.9rem; margin-bottom: 2rem; }
  .lecture { background: #fff; border-radius: 10px; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }
  .lecture-header { padding: 1rem 1.2rem; cursor: pointer; display: flex; align-items: center; justify-content: space-between; user-select: none; transition: background 0.15s; }
  .lecture-header:hover { background: #f8f9fb; }
  .lecture-header h2 { font-size: 1.05rem; font-weight: 600; }
  .lecture-header .count { font-size: 0.8rem; color: #888; margin-left: 0.6rem; }
  .chevron { font-size: 0.7rem; color: #999; transition: transform 0.2s; }
  .lecture.open .chevron { transform: rotate(90deg); }
  .notebook-list { display: none; padding: 0 1.2rem 0.8rem; }
  .lecture.open .notebook-list { display: block; }
  .nb { display: flex; align-items: center; padding: 0.55rem 0; border-top: 1px solid #f0f0f0; gap: 0.6rem; }
  .nb:first-child { border-top: none; }
  .nb-title { flex: 1; font-size: 0.92rem; }
  .nb-title a { color: #1a1a2e; text-decoration: none; cursor: pointer; }
  .nb-title a:hover { color: #4361ee; }
  .badge { display: inline-block; font-size: 0.65rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; padding: 0.15em 0.5em; border-radius: 4px; background: #e8ecf4; color: #667; vertical-align: middle; margin-left: 0.4rem; }
  .btn-colab { font-size: 0.75rem; padding: 0.3em 0.7em; border-radius: 5px; background: #f9ab00; color: #fff; text-decoration: none; font-weight: 600; white-space: nowrap; transition: background 0.15s; }
  .btn-colab:hover { background: #e09800; }
  .settings { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .settings label { font-size: 0.82rem; color: #555; }
  .settings select, .settings input { font-size: 0.82rem; padding: 0.3em 0.5em; border: 1px solid #ccc; border-radius: 5px; background: #fff; }
  .settings input { width: 14rem; }
  .help-toggle { display: inline-block; font-size: 0.8rem; color: #4361ee; cursor: pointer; margin-left: auto; padding: 0.3em 0.7em; border: 1px solid #d0d7e8; border-radius: 5px; background: #f8f9fb; user-select: none; }
  .help-toggle:hover { background: #edf0f7; }
  .help-panel { display: none; background: #fff; border: 1px solid #e0e4ed; border-radius: 10px; padding: 1.3rem 1.5rem; margin-bottom: 1.5rem; font-size: 0.85rem; line-height: 1.65; color: #333; }
  .help-panel.open { display: block; }
  .help-panel h3 { font-size: 0.95rem; margin: 1rem 0 0.4rem; color: #1a1a2e; }
  .help-panel h3:first-child { margin-top: 0; }
  .help-panel code { background: #f0f2f7; padding: 0.15em 0.45em; border-radius: 4px; font-size: 0.82rem; font-family: "SF Mono", Menlo, Consolas, monospace; }
  .help-panel pre { background: #1a1a2e; color: #e8ecf4; padding: 0.7em 1em; border-radius: 6px; overflow-x: auto; margin: 0.4rem 0 0.6rem; font-size: 0.8rem; line-height: 1.5; }
  .help-panel ol, .help-panel ul { padding-left: 1.4em; margin: 0.3rem 0; }
  .help-panel li { margin: 0.2rem 0; }
  .help-section { border-top: 1px solid #f0f0f0; padding-top: 0.8rem; margin-top: 0.8rem; }
  .help-section:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
</style>
</head>
<body>
<h1>CS4248 &mdash; Natural Language Processing</h1>
<p class="subtitle">Click a lecture to expand. Notebook links open in Jupyter; Colab buttons open in Google Colab.</p>

<div class="settings">
  <label>Jupyter:</label>
  <select id="jupyter-mode" onchange="updateLinks()">
    <option value="lab">JupyterLab</option>
    <option value="classic">Classic Notebook</option>
  </select>
  <label>Base URL:</label>
  <input id="jupyter-base" type="text" value="http://localhost:8888" onchange="updateLinks()" />
  <span class="help-toggle" onclick="document.getElementById('help').classList.toggle('open')">? Setup Guide</span>
</div>

<div class="help-panel" id="help">

  <div class="help-section">
    <h3>1. Install Jupyter</h3>
    <p>Check if Jupyter is already installed:</p>
    <pre>jupyter --version</pre>
    <p>If not found, install via pip:</p>
    <pre># JupyterLab (recommended &mdash; modern interface)
pip install jupyterlab

# Classic Notebook
pip install notebook</pre>
    <p>Verify after installing:</p>
    <pre># Should print a version number
jupyter lab --version
jupyter notebook --version</pre>
  </div>

  <div class="help-section">
    <h3>2. Start the Jupyter Server</h3>
    <p>Open a terminal, <code>cd</code> into the <strong>Notebooks</strong> folder, and start Jupyter:</p>
    <pre>cd path/to/CS4248/Notebooks

# Option A &mdash; JupyterLab
jupyter lab

# Option B &mdash; Classic Notebook
jupyter notebook</pre>
    <p>This launches a local server (usually at <code>http://localhost:8888</code>) and opens a browser tab. If the port is different (e.g. <code>8889</code>), update the <strong>Base URL</strong> field above to match.</p>
  </div>

  <div class="help-section">
    <h3>3. Use This Page</h3>
    <ol>
      <li>Make sure Jupyter is running (step 2).</li>
      <li>Select <strong>JupyterLab</strong> or <strong>Classic Notebook</strong> in the dropdown above, matching whichever you started.</li>
      <li>Click any notebook title &mdash; it opens directly in your running Jupyter server.</li>
      <li>The yellow <strong>Colab</strong> button opens the original GitHub version in Google Colab (no local setup needed, but local data files won't be available).</li>
    </ol>
  </div>

  <div class="help-section">
    <h3>Troubleshooting</h3>
    <ul>
      <li><strong>Notebook shows raw JSON?</strong> &mdash; Jupyter isn't running, or the Base URL is wrong.</li>
      <li><strong>"File not found" in Jupyter?</strong> &mdash; You started Jupyter from a different folder. Restart it from the <code>Notebooks/</code> directory.</li>
      <li><strong>Port conflict?</strong> &mdash; If 8888 is in use, Jupyter picks the next available port (8889, 8890...). Check the terminal output and update the Base URL.</li>
    </ul>
  </div>

</div>
""")

    for idx, lec in enumerate(lectures):
        num = lec['lecture_num']
        title = _html_escape(lec['lecture_title'])
        count = len(lec['notebooks'])
        open_cls = ' open' if idx == 0 else ''

        parts.append(f'<div class="lecture{open_cls}" id="lec{num}">')
        parts.append(f'  <div class="lecture-header" onclick="toggle(\'lec{num}\')">')
        parts.append(f'    <div><h2>Lecture {num}: {title}</h2><span class="count">{count} notebook{"s" if count != 1 else ""}</span></div>')
        parts.append(f'    <span class="chevron">&#9654;</span>')
        parts.append(f'  </div>')
        parts.append(f'  <div class="notebook-list">')

        for nb in lec['notebooks']:
            folder = f'Lecture{num}'
            data_path = f'{folder}/{nb["filename"]}'
            display = _html_escape(nb['name'])
            colab_url = _colab_url(nb['github_url'])
            badge = '<span class="badge">optional</span>' if nb['optional'] else ''

            parts.append(f'    <div class="nb">')
            parts.append(f'      <div class="nb-title"><a class="nb-link" data-path="{data_path}">{display}</a>{badge}</div>')
            parts.append(f'      <a class="btn-colab" href="{colab_url}" target="_blank">Colab</a>')
            parts.append(f'    </div>')

        parts.append(f'  </div>')
        parts.append(f'</div>')
        parts.append('')

    parts.append("""\
<script>
function toggle(id) {
  document.getElementById(id).classList.toggle('open');
}

function updateLinks() {
  var base = document.getElementById('jupyter-base').value.replace(/\\/+$/, '');
  var mode = document.getElementById('jupyter-mode').value;
  document.querySelectorAll('a.nb-link').forEach(function(a) {
    var path = a.getAttribute('data-path');
    var encoded = path.split('/').map(encodeURIComponent).join('/');
    if (mode === 'lab') {
      a.href = base + '/lab/tree/' + encoded;
    } else {
      a.href = base + '/notebooks/' + encoded;
    }
    a.target = '_blank';
  });
  localStorage.setItem('cs4248-jupyter-base', base);
  localStorage.setItem('cs4248-jupyter-mode', mode);
}

// Restore saved settings
(function() {
  var savedBase = localStorage.getItem('cs4248-jupyter-base');
  var savedMode = localStorage.getItem('cs4248-jupyter-mode');
  if (savedBase) document.getElementById('jupyter-base').value = savedBase;
  if (savedMode) document.getElementById('jupyter-mode').value = savedMode;
  updateLinks();
})();
</script>
</body>
</html>
""")

    return '\n'.join(parts)


def _html_escape(text: str) -> str:
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace(' — ', ' &mdash; ')
        .replace('—', '&mdash;')
    )


def _colab_url(github_url: str) -> str:
    """Convert GitHub blob URL to Colab URL."""
    return github_url.replace(
        'https://github.com/', 'https://colab.research.google.com/github/'
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <list_file> [output_dir]')
        sys.exit(1)

    list_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else '.'

    print('=== Notebook Pipeline ===')
    print(f'List file: {list_file}')
    print(f'Output:    {output_dir}')
    print()

    # Step 1
    print('[1/6] Parsing list file...')
    lectures = parse_list(list_file)
    total = sum(len(l['notebooks']) for l in lectures)
    print(f'  Found {len(lectures)} lectures, {total} notebooks')
    print()

    # Step 2
    print('[2/6] Cloning repositories...')
    with tempfile.TemporaryDirectory() as tmp_dir:
        repos = clone_repos(lectures, tmp_dir)
        print(f'  Cloned {len(repos)} repos')
        print()

        # Step 3
        print('[3/6] Cleaning & copying notebooks...')
        clean_and_copy(lectures, repos, output_dir)
        print()

        # Step 4
        print('[4/6] Merging assets into common/...')
        merge_assets(lectures, repos, output_dir)
        print()

    # Step 5
    print('[5/6] Fixing notebook paths...')
    fix_notebook_paths(lectures, output_dir)
    print()

    # Step 6
    print('[6/6] Generating index.html...')
    generate_index(lectures, output_dir)
    print()

    print('=== Done ===')


if __name__ == '__main__':
    main()
