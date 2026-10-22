# Capability: file-uploads

## In plain words
Letting users upload and download files (images, documents, videos). Files can carry malware, be huge, or expose private data.

## Trigger words
upload, image, photo, avatar, file, attachment, document, PDF, video, download

## Safe defaults
- Store files in object storage (S3/R2/GCS/Supabase Storage), not on the app server or in the database
- Upload directly to storage with short-lived signed URLs; private by default, served via signed URLs
- Validate type by content (not just extension) and enforce size limits; randomize stored names
- Scan uploads for malware if shared with others; strip image metadata (EXIF location) when appropriate
- Generate thumbnails/transcodes in a background job; set retention and deletion rules
- Authorization on downloads, same as other data

## Must test
- Oversized and wrong-type files are rejected
- User A cannot fetch user B's file or guess its URL
- Interrupted uploads don't leave orphan records
- Deleting a record deletes (or schedules deletion of) the file

## Common mistakes
Public buckets; trusting file extensions; storing on local disk of a stateless server; no size limits; serving user HTML/SVG from your domain; leaking location in EXIF; no cleanup

## Don't build it yourself
File serving and CDN. Use object storage with signed URLs.
