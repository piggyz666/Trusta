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

// Builds: the backend-served copy (default) or the public static website (`npm run build:site`).
export const STATIC_SITE = import.meta.env.VITE_SITE === "static";
export const HOME = STATIC_SITE ? "/" : "/intro.html";
// Flip to true once the repository is public: download buttons, GitHub links and install commands go live on the website.
export const REPO_PUBLIC = true;
export const SHOW_REPO = REPO_PUBLIC || !STATIC_SITE;
// Flip to true once a release with TRUSTA-windows-x64.zip exists (the "Packages" workflow on a v* tag); until then the
// download button says "coming soon" instead of linking to a file that isn't there.
export const RELEASE_READY = true;
