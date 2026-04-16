"""Account views."""

from django.shortcuts import render
from django.views.generic import DetailView

from accounts.models import Profile

class ProfileView(DetailView):
    model = Profile
    template_name = "accounts/profile.html"
