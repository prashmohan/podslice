from django.urls import path
from .views import (
    PodcastSubscriptionAPIView, 
    PodcastRSSFeedView, 
    PodcastStatusAPIView,
    PodcastSubscribeUIView,
    PodcastStatusUIView
)

urlpatterns = [
    path('api/subscribe/', PodcastSubscriptionAPIView.as_view(), name='podcast-subscribe-api'),
    path('api/rss/<uuid:podcast_id>/', PodcastRSSFeedView.as_view(), name='podcast-rss-feed-api'),
    path('api/status/<uuid:podcast_id>/', PodcastStatusAPIView.as_view(), name='podcast-status-api'),
    path('', PodcastSubscribeUIView.as_view(), name='podcast-subscribe-ui'),
    path('status/<uuid:podcast_id>/', PodcastStatusUIView.as_view(), name='podcast-status-ui'),
]
