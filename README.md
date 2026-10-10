# Local AI Monitoring

Monitor for llama-server on an AMD Strix Halo host. It shows CPU, amdgpu load, GTT, and the questions sent through LiteLLM or directly to llama-server.

LiteLLM stays the source for finished prompts. The dashboard only reads `LiteLLM_SpendLogs` and does not store the text a second time. Live generation comes from `GET /slots` and `GET /metrics`.

## llama-server

Each server needs both endpoints:

```bash
llama-server --slots --metrics -m /models/model.gguf --host 127.0.0.1 --port 8080
```

A llama-server router is queried only for models whose status is `loaded`, with `autoload=false`. Sleeping and unloaded models are left alone.

## Configuration

```bash
cp config.example.yaml config.yaml
```

`model` is the name LiteLLM uses for that model. It joins a live slot request with the later SpendLogs row.

`language` is the UI language when the browser has no saved choice. `en` is the default. `de` is included. Add another language by creating `web/src/i18n/<code>.ts` with the same catalog as `en.ts` and registering it in `web/src/i18n/index.tsx`. The header switch then offers it. A visitor's choice is stored in the browser and overrides this default.

The header also switches between light and dark. With no saved choice, the page follows the browser. A click stores light or dark for the next visit.

Create a Postgres user that can only read. For SQLite, set `litellm_database_url` to the LiteLLM database file, for example `sqlite:////path/to/litellm.db`.

Calls that bypass LiteLLM stay in `direct_history_path`. That is the only local list.

## Local, without the APU

Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
HALO_FIXTURES=1 .venv/bin/halo-dash
```

The UI is then at `http://127.0.0.1:8787` and shows sample data.

## Podman on the Strix host

```bash
podman build -t localhost/halo-dash:latest -f Containerfile .
mkdir -p ~/.config/halo-dash ~/.local/share/halo-dash
cp config.example.yaml ~/.config/halo-dash/config.yaml
cp deploy/halo-dash.container ~/.config/containers/systemd/halo-dash.container
systemctl --user daemon-reload
systemctl --user start halo-dash
```

The container uses the host network and the host PID namespace, so llama-server on localhost and the machine's CPU are visible. `/sys` is mounted read-only. `/etc/os-release` and `/etc/hostname` are the host's files, so the UI shows the machine rather than the image. If the LiteLLM database is SQLite, add that directory to the Quadlet `Volume` lines.

Firewall, only from the nginx machine:

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="NGINX_IP" port port="8787" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Copy the `location` block from `deploy/nginx.conf.example` into the existing nginx. Authentication stays there.

## License

Copyright (C) 2026 network.publishing Möller-Westbunk GmbH. Author: Christoph Möller <hallo@network-publishing.de>. Licensed under the GNU Affero General Public License v3.0. See `LICENSE`.

## GitHub mirror

GitLab `main` stays the development history. GitHub receives a separate history. The first publish is one commit, `Initial GitHub release`. Each later publish squashes every GitLab commit since that sync into one English commit. Feature branches are not published.

Write GitLab commit messages in English from here on. The public squash commit still needs its own English summary.

Add the GitHub remote once. Create the GitHub repository empty, without a generated README or license.

```bash
git remote add github git@github.com:ORG/strix-halo-dashboard.git
```

On `main`, after it matches `origin/main`:

```bash
scripts/publish-github.sh
scripts/publish-github.sh -m "English summary of the merged changes"
scripts/publish-github.sh --dry-run -m "English summary of the merged changes"
```

The first command is only for the empty GitHub repository. Later commands require `-m`. `--dry-run` prints the GitLab commits that would be squashed and does not push.

GitLab CI has a manual job `publish-github` on `main` only. Set the protected variables `GITHUB_REPOSITORY` and `GITHUB_TOKEN`. Set `GITHUB_RELEASE_MESSAGE` when running the job after the first publish.
