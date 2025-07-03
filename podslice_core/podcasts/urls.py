from django.urls import path
from .views import (
    PodcastSubscriptionAPIView, 
    PodcastRSSFeedView, 
    PodcastStatusAPIView,
    PodcastSubscribeUIView,
    PodcastStatusUIView,
    PodcastDeleteAPIView,
    PodcastRefreshView,
    PodcastReprocessView,
    serve_rehosted_media
)

urlpatterns = [
    path('api/subscribe/', PodcastSubscriptionAPIView.as_view(), name='podcast-subscribe-api'),
    path('api/rss/<uuid:podcast_id>/rss.xml', PodcastRSSFeedView.as_view(), name='podcast-rss-feed-api'),
    path('api/status/<uuid:podcast_id>/', PodcastStatusAPIView.as_view(), name='podcast-status-api'),
    path('api/podcasts/<uuid:podcast_id>/', PodcastDeleteAPIView.as_view(), name='podcast-delete-api'),
    path('', PodcastSubscribeUIView.as_view(), name='podcast-subscribe-ui'),
    path('status/<uuid:podcast_id>/', PodcastStatusUIView.as_view(), name='podcast-status-ui'),
    path('status/<uuid:podcast_id>/refresh/', PodcastRefreshView.as_view(), name='podcast-refresh'),
    path('status/<uuid:podcast_id>/reprocess/', PodcastReprocessView.as_view(), name='podcast-reprocess'),
    path('media/episodes/<uuid:media_guid>/', serve_rehosted_media, name='serve_media_episode'),
]

