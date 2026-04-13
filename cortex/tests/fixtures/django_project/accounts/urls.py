"""Account URL patterns."""

from django.urls import path
from accounts.views import ProfileView

urlpatterns = [
    path("profile/<int:pk>/", ProfileView.as_view(), name="profile"),
]
