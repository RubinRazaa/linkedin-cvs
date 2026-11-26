# linkedin-cvs

Download the CV of everyone who applied to your LinkedIn job posts, with one command or one function call. It can also save the job description and details of any public job post, with no login.

It works with the standard LinkedIn hiring dashboard (`linkedin.com/hiring`). It does not work with LinkedIn Recruiter.

> **Read this first.** This client uses LinkedIn's private, undocumented requests. LinkedIn's terms prohibit automated access, and an account that runs this can be restricted. LinkedIn can also change these requests at any time, which will stop the client until it is updated. See [Risks and limits](#risks-and-limits).

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Log in (one time)](#log-in-one-time)
- [Command line](#command-line)
- [Python API](#python-api)
- [Job description (no login)](#job-description-no-login)
- [Where the files go](#where-the-files-go)
- [Running on a server](#running-on-a-server)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Risks and limits](#risks-and-limits)
- [Development](#development)
- [License](#license)

## Requirements

- Python 3.11 or newer
- Google Chrome installed
- A LinkedIn account that can see the job posts (the job owner)
- A desktop session: the client opens a real Chrome window. On a server with no monitor, use a virtual display (see [Running on a server](#running-on-a-server))

## Install

```bash
pip install git+https://github.com/RubinRazaa/linkedin-cvs
```

Or, from a copy of this repository:

```bash
pip install .
```

This installs the `linkedin_cvs` Python module and the `linkedin-cvs` command.

To run it without installing, use `uv run linkedin_cvs.py` in place of `linkedin-cvs` in every command below.

## Log in (one time)

```bash
linkedin-cvs --login
```

1. A Chrome window opens on LinkedIn.
2. Sign in. Enter any verification code LinkedIn asks for.
3. Check that **Manage job posts** shows your jobs. Dismiss any prompt that covers them, such as the two-factor reminder.
4. Close the window.

The login is saved in `~/.linkedin_cvs/` and reused on every later run. It is separate from your normal Chrome profiles. There are no cookies to copy.

Run `--login` again only when LinkedIn logs the session out, or to switch to another account (sign out in that window, then sign in as the other account).

## Command line

```bash
linkedin-cvs                          # every open job post, everyone who applied
linkedin-cvs 1234567890               # only this job post
linkedin-cvs 1234567890 2345678901    # several job posts
linkedin-cvs --limit 3                # at most 3 new CVs per job (good for a first test)
linkedin-cvs --out /data/cvs          # save somewhere else (default: ./cvs)
linkedin-cvs --login                  # open the window to log in or clear a prompt
linkedin-cvs --jd 1234567890          # job description and details only; no login, no Chrome
linkedin-cvs --selftest               # check the parsing logic; does not contact LinkedIn
```

The job ID is the number after `jobId=` in the job's LinkedIn URL.

Example output:

```
1 job posts: 1234567890
job 1234567890: 12 applicants, 3 to download
  saved Jane_Doe_30000000001.pdf
  saved Sam_Lee_30000000002.pdf
  saved Ana_Silva_30000000003.pdf
done: 3 CVs saved under cvs/
```

Start with `--limit 3` on one job. Run the full download only after that works.

## Python API

```python
from linkedin_cvs import download_cvs, Stopped

try:
    files = download_cvs()
except Stopped as error:
    print(f"LinkedIn run stopped: {error}")
    files = []

for path in files:
    job_id = path.parent.name
    # upload `path` to the Drive folder for `job_id`
```

### `download_cvs(job_ids=None, out="cvs", limit=None)`

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `job_ids` | list of str, or `None` | `None` | Job posts to download. `None` means every job on the **Open** tab. |
| `out` | str or path | `"cvs"` | Folder to save into. A subfolder per job is created inside it. |
| `limit` | int, or `None` | `None` | Download at most this many new CVs per job. `None` means all. |

**Returns** a list of `pathlib.Path`, one per CV saved **in this call**. CVs that were already in the folder are skipped and not returned, so the list is exactly the new files to upload.

**Raises** `Stopped` when the run cannot continue: the session is logged out, LinkedIn returned an unexpected response, or no open job posts are visible. The client never retries after this. Files saved before the stop stay on disk, and the next call continues from there.

The function blocks until the run finishes. Expect roughly 4 to 9 seconds per CV.

## Job description (no login)

```bash
linkedin-cvs --jd 1234567890 2345678901
```

This reads LinkedIn's public job page, so it needs no login and no Chrome, and it does not touch your LinkedIn account. It works for any job post that is publicly visible, including other companies' posts. You must pass the job IDs: without a login the client cannot list your job posts.

Each job gets two files in its folder: `job.json` with every field, and `job.txt` with the title, company, location, link and description as plain text.

```python
from linkedin_cvs import job_details, download_jds

job = job_details("1234567890")       # returns a dict, saves nothing
paths = download_jds(["1234567890"])  # saves the files, returns the job.json paths
```

| Field | Example |
|---|---|
| `job_id`, `url` | `"1234567890"`, `"https://www.linkedin.com/jobs/view/1234567890"` |
| `title` | `"Backend Engineer"` |
| `company` | `"Example Ltd"` |
| `location` | `"London, England, United Kingdom"` |
| `posted` | `"1 week ago"` (relative, as LinkedIn shows it) |
| `applicants` | `"58 applicants"` (LinkedIn's public count, often rounded) |
| `criteria` | `{"Seniority level": "...", "Employment type": "...", "Job function": "...", "Industries": "..."}` |
| `description` | The full description as plain text |
| `description_html` | The same with LinkedIn's formatting tags |

A field LinkedIn does not show comes back empty. Both functions raise `Stopped` if the job is not public or LinkedIn refuses the request. Several requests in quick succession get `HTTP 429`; wait a minute and run again.

## Where the files go

```
cvs/
  1234567890/                       one folder per job post, named by job ID
    Jane_Doe_30000000001.pdf        <Applicant_Name>_<applicationId>.pdf
    Sam_Lee_30000000002.pdf
    job.json                        only with --jd: job details
    job.txt                         only with --jd: readable description
```

- The number at the end of each filename is LinkedIn's application ID. The client uses it to know which CVs it already has, so do not rename files inside `out` if you want re-runs to skip them.
- If LinkedIn's page layout changes and names cannot be read, files are named `<applicationId>.pdf`.
- All files are PDF. LinkedIn serves its own PDF copy of each resume.
- CVs are personal data. Keep the output folder out of git (the included `.gitignore` does this) and store it according to your data-protection rules.

## Running on a server

The client needs a display, because it runs a normal Chrome window. On a Linux server with no monitor it creates a virtual one by itself, as long as `xvfb` is installed. Chrome's own "headless" mode is not used: it identifies itself to websites as headless.

A login from a data-center address looks new to LinkedIn. Expect a verification challenge and a higher chance of the account being restricted than on an office machine.

**Setup (once)**, as a normal user, not root:

```bash
sudo apt install -y xvfb x11vnc libatomic1
pip install git+https://github.com/RubinRazaa/linkedin-cvs
playwright install --with-deps chrome
```

**Log in (once)**. The saved login cannot be copied from another machine, so do it on the server through a remote view of the virtual screen:

```bash
# on the server
linkedin-cvs --login &                         # starts the virtual screen :99 and Chrome
sleep 5 && x11vnc -display :99 -localhost -nopw -once &
```

```bash
# on your own computer
ssh -L 5900:localhost:5900 youruser@yourserver
```

Then connect a VNC viewer to `localhost:5900` (on a Mac: Finder, **Go > Connect to Server**, `vnc://localhost:5900`). Sign in to LinkedIn, then press `Ctrl+W` to close the tab. The remote view only travels through the SSH connection.

**Every run after that:**

```bash
linkedin-cvs
```

The same command as on a desktop. When Linux has no display, the client starts a virtual one (`Xvfb` on `:99`) by itself and stops it afterwards. This also applies when your own code calls `download_cvs()`.

## How it works

1. Chrome opens with the saved login and loads **Manage job posts**.
2. The client reads the open job IDs from the job-posts page data.
3. For each job, it reads the applicant list for the three stages the dashboard shows (In Review, Shortlist, Rejected), 30 applicants per page.
4. For each applicant it does not already have, it asks LinkedIn for the resume viewer, takes the signed PDF link from the response, and downloads the PDF.
5. Chrome closes.

All requests are made from inside the logged-in LinkedIn page, so the session never leaves the browser. The client only reads: it never rates, shortlists, rejects or messages anyone.

It waits a random 4 to 9 seconds between requests. Do not lower this.

## Troubleshooting

| Message or symptom | Meaning | What to do |
|---|---|---|
| `Log in to LinkedIn in the Chrome window that just opened` | No saved login, or it expired. | Sign in within 5 minutes, or run `linkedin-cvs --login`. |
| `Stopped: no open job posts visible` | The job-posts page shows no open jobs. A LinkedIn prompt may be covering it, or this account has none. | Run `linkedin-cvs --login` and look at the page. Or pass job IDs directly. |
| `Stopped: HTTP ... (logged out, or LinkedIn challenged the request)` | LinkedIn refused a request. | Run `linkedin-cvs --login` and check the account for a verification or restriction notice. Wait before running again. |
| `Stopped: resume link returned HTTP ..., not a PDF` | The download link did not return a resume. | Same as above. |
| `job ...: 0 applicants` for a job that has applicants | The logged-in account cannot see that job's applicants. | Log in with the account that owns the job. |
| `Stopped: job ...: HTTP 429` (with `--jd`) | Too many public requests in a short time. | Wait a minute, then run again. |
| `no resume: <name>` | That applicant has no resume attached. | Nothing; the run continues. |
| Error about the profile already being in use | A client Chrome window is still open. | Close it, then run again. |
| Files are named by ID only | Names could not be read, for example LinkedIn is not set to English. | The CVs are still correct. Set the account language to English for names. |
| It worked before and now finds nothing | LinkedIn changed its private requests. | The client needs updating against the new page. |

## Risks and limits

- **Account risk.** Automated access breaks LinkedIn's terms. The usual outcome if flagged is a forced re-login or a temporary restriction; a permanent ban is possible. Keep runs small, run from your usual machine and network where you can, and do not schedule frequent runs.
- **Unofficial.** These are private requests with no stability promise.
- **Open jobs only by default.** Closed and draft jobs are not downloaded unless you pass a closed job's ID.
- **Applicants who applied on your company website** have no CV on LinkedIn.
- **English page text** is used to read applicant names.

## Development

The client is one file, `linkedin_cvs.py`.

```bash
uv run linkedin_cvs.py --selftest    # parsing checks, no network
uv build                             # rebuild dist/
```

If LinkedIn changes its page, the pieces most likely to need updating are the three patterns in `parse_applicants` and `parse_pdf_url`, and the request paths in the `Client` class.

## License

MIT. See [LICENSE](LICENSE).
