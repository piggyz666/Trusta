// Every outward link the site uses lives here. While the repository is private these only work for
// signed-in collaborators; when it goes public (or moves), change REPO and everything follows.
export const REPO = "piggyz666/Trusta";
export const BRANCH = "main";
export const GITHUB = `https://github.com/${REPO}`;
export const RAW = `https://raw.githubusercontent.com/${REPO}/${BRANCH}`;
export const RELEASES = `${GITHUB}/releases`;
export const DOWNLOAD_WINDOWS = `${GITHUB}/releases/latest/download/TRUSTA-windows-x64.zip`;
export const ONE_LINER_WINDOWS = `irm ${RAW}/install.ps1 | iex`;
export const ONE_LINER_UNIX = `curl -fsSL ${RAW}/install.sh | bash`;
export const CLONE = `git clone ${GITHUB}.git`;
export const DOCS = "/docs.html";
