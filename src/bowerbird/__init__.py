"""Collects the CVs of applicants to your LinkedIn job posts, one folder per job."""
from .core import Stopped
from .cvs import download, login
from .jobs import download_jds, job_details

__all__ = ["Stopped", "download", "download_jds", "job_details", "login"]
