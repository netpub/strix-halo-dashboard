# GitHub release

Staff notes for publishing the GitHub mirror. This is not part of the dashboard documentation.

GitLab `main` stays the development history. GitHub receives a separate history. The first publish is one commit, `Initial GitHub release`. Each later publish squashes every GitLab commit since that sync into one English commit. Feature branches are not published.

Write GitLab commit messages in English from here on. The public squash commit still needs its own English summary.

Add the GitHub remote once. The repository must be empty, without a generated README or license.

```bash
git remote add github https://github.com/netpub/strix-halo-dashboard.git
```

On `main`, after it matches `origin/main`:

```bash
scripts/publish-github.sh
scripts/publish-github.sh -m "English summary of the merged changes"
scripts/publish-github.sh --dry-run -m "English summary of the merged changes"
```

The first command is only for the empty GitHub repository. Later commands require `-m`. `--dry-run` prints the GitLab commits that would be squashed and does not push.

GitLab CI has a manual job `publish-github` on `main` only. Set the protected variables `GITHUB_REPOSITORY` and `GITHUB_TOKEN`. Set `GITHUB_RELEASE_MESSAGE` when running the job after the first publish.
