import os
from django.http import FileResponse, Http404
from .models import RehostedMedia

def serve_rehosted_media(request, media_guid):
    """
    Serves a re-hosted audio file.

    This view is the heart of the podslice_rehost service. Its only job
    is to look up a media GUID in its database, find the corresponding
    physical file path on disk, and stream the file back to the client.

    Args:
        request: The Django HttpRequest object.
        media_guid: The UUID of the media file to be served.

    Returns:
        A FileResponse object that streams the media file, or raises
        an Http404 exception if the media is not found.
    """
    # Step 1: Look up the media item using the primary key (media_guid).
    # This is an efficient database query.
    try:
        media_item = RehostedMedia.objects.get(pk=media_guid)
    except RehostedMedia.DoesNotExist:
        # If no record is found, it's a 404. Do not proceed.
        raise Http404("Media file record not found in the database.")

    # Step 2: Perform a crucial security and integrity check.
    # Verify that the file path stored in the database actually points
    # to a file that exists on the server's filesystem.
    if not os.path.exists(media_item.file_path):
         # This could happen if a file was deleted manually.
         # Log this error in a real production system.
         raise Http404("The media file is registered but was not found on disk.")

    # Step 3: Use Django's FileResponse.
    # This is the most efficient way to serve large files, as it streams
    # the file from disk directly to the response without loading the
    # entire file into memory. This is critical for performance and
    # for handling large podcast episodes.
    response = FileResponse(
        open(media_item.file_path, 'rb'),
        content_type=media_item.content_type
    )
    return response