"""Blog URL patterns."""

from django.urls import path
from blog.views import PostListView, PostDetailView

urlpatterns = [
    path("", PostListView.as_view(), name="post_list"),
    path("<slug:slug>/", PostDetailView.as_view(), name="post_detail"),
]
