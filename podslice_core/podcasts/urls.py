from django.urls import path
from .views import PodcastSubscriptionAPIView, PodcastRSSFeedView, PodcastStatusAPIView

urlpatterns = [
    path('subscribe/', PodcastSubscriptionAPIView.as_view(), name='podcast-subscribe'),
    path('rss/<uuid:podcast_id>/', PodcastRSSFeedView.as_view(), name='podcast-rss-feed'),
    path('status/<uuid:podcast_id>/', PodcastStatusAPIView.as_view(), name='podcast-status'),
]