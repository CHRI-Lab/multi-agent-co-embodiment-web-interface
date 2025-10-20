## 🚀 Setup
 
```bash
uv python install 3.13.0 --force
uv venv --python 3.13 .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel "pip-tools==7.4.1"

 pip-compile --no-header --no-annotate --strip-extras \
-o requirements.txt requirements.in

pip install -r requirements.txt

python app.py
```