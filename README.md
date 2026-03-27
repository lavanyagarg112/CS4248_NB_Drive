# CS4248 Lecture Notebooks

## Option 1: Google Colab (no setup)

1. Open `index.html` in your browser
2. Click the **Colab** button next to any notebook — opens a standalone version that works in Colab
3. Notebooks marked **Local only** don't have standalone versions and may not work in Colab

> Note: Colab runs a copy — your changes only exist in that session. They don't affect the originals.

## Option 2: Local Jupyter

```bash
git clone <this-repo-url>
cd Notebooks
python fetch_notebooks.py list.txt .
jupyter lab
```

Then open `index.html` in your browser and click any notebook link.

To reset notebooks to their original state (discard local edits):
```bash
python fetch_notebooks.py list.txt .
```

### Requirements
- Python 3
- JupyterLab (`pip install jupyterlab`) or Jupyter Notebook (`pip install notebook`)
