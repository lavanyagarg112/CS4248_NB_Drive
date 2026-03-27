# CS4248 Lecture Notebooks

## Option 1: Google Colab (no setup)

1. Open `index.html` in your browser (or visit the GitHub Pages link)
2. Click the **Colab** button next to any notebook
3. To save your work: **File > Save a copy in Drive**
4. Notebooks marked **Local only** don't have standalone versions and may not work in Colab

## Option 2: Local Jupyter

```bash
git clone <this-repo-url>
cd Notebooks
python fetch_notebooks.py list.txt .
```

Then start Jupyter:
```bash
# JupyterLab (recommended)
jupyter lab

# OR Classic Notebook
jupyter notebook
```

Open `index.html` in your browser and click any notebook link.

To reset notebooks to their original state (discard local edits):
```bash
python fetch_notebooks.py list.txt .
```

### Requirements
- Python 3
- JupyterLab (`pip install jupyterlab`) or Jupyter Notebook (`pip install notebook`)
