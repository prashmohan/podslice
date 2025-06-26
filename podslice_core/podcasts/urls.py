from django.urls import path
from .views import PodcastSubscriptionAPIView, PodcastRSSFeedView

urlpatterns = [
    path('podcasts/', PodcastSubscriptionAPIView.as_view(), name='podcast-subscribe'),
    path('feeds/podcasts/<uuid:podcast_id>/rss.xml', PodcastRSSFeedView.as_view(), name='podcast-rss-feed'),
    path('podcasts/<uuid:podcast_id>/status/', PodcastStatusAPIView.as_view(), name='podcast-status'),
]