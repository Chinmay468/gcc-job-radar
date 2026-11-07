"""ATS API clients module."""

from gcc_job_radar.clients.amazon import AmazonClient
from gcc_job_radar.clients.apple import AppleClient
from gcc_job_radar.clients.ashby import AshbyClient
from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.clients.ea import EAClient
from gcc_job_radar.clients.greenhouse import GreenhouseClient
from gcc_job_radar.clients.lever import LeverClient
from gcc_job_radar.clients.microsoft import MicrosoftClient
from gcc_job_radar.clients.phenom_successfactors import PhenomSuccessFactorsClient
from gcc_job_radar.clients.smartrecruiters import SmartRecruitersClient
from gcc_job_radar.clients.uber import UberClient
from gcc_job_radar.clients.workday import WorkdayClient

__all__ = [
    "BaseATSClient",
    "AmazonClient",
    "AppleClient",
    "EAClient",
    "GreenhouseClient",
    "LeverClient",
    "AshbyClient",
    "MicrosoftClient",
    "SmartRecruitersClient",
    "UberClient",
    "WorkdayClient",
    "PhenomSuccessFactorsClient",
]

