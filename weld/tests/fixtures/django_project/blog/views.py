"""Blog views."""

from django.shortcuts import render, get_object_or_404
from django.views.generic import ListView, DetailView

from blog.models import Post

class PostListView(ListView):
    model = Post
    template_name = "blog/post_list.html"
    queryset = Post.objects.filter(published=True)

class PostDetailView(DetailView):
    model = Post
    template_name = "blog/post_detail.html"
