FaceSort - sort and tag your photos by the people in them
==========================================================

Everything runs on your own PC. No photos, faces or names are ever uploaded.


INSTALL / UPDATE
----------------
1. Right-click the zip > Properties > tick "Unblock" > OK  (stops Windows warnings),
   then right-click > Extract All...  (to update, extract over your old FaceSort
   folder and choose Replace).
2. Double-click  Setup.bat
   - If Windows shows "Windows protected your PC", click More info > Run anyway.
   - Setup installs Python if needed, downloads the components and builds
     FaceSort.exe. First time about 5 minutes; updates about 2 minutes.
3. FaceSort starts automatically. Next time use the desktop shortcut or FaceSort.exe.

FaceSort.exe is self-contained; you can move it anywhere. After setup you may delete
the ".venv" folder here to free about 700 MB (only needed to rebuild the exe).


WHERE YOUR DATA LIVES (never inside the app folder, so updates can't touch it)
---------------------------------------------------------------------------
%LOCALAPPDATA%\FaceSort\library.db         scanned photos and faces
%LOCALAPPDATA%\FaceSort\known_people.db    everyone you've named (see below)
%LOCALAPPDATA%\FaceSort\settings.json      your settings
%LOCALAPPDATA%\FaceSort\facesort.log       error/crash details, if anything goes wrong


USING IT
--------
1. Add folder...  - FaceSort scans the photos for faces. New photos are picked up
   every time it starts.
2. Unnamed groups - look-alike faces. Remove any that don't belong (R), then
   "Name this group...".
3. Suggestions - once someone has a name, matching faces get an orange "?" border.
   Open the person and "Confirm all suggestions", or press C / R per face.
4. Write tags - saves names into the photos (search Windows Explorer with  tags:Mom ).
5. Folders - your save layout. Folders can be nested, people can be placed in a
   folder, and each person can have their own folders (see "Folders" below).
   Press A on selected photos, or use the Category bar in the preview, to give photos
   a folder ("Wedding 2024", "Beach"). Folders are only used for saving - they are
   no longer written into the photo files as tags (Write tags removes the category
   tags older versions wrote).
6. Sort into folders... - copies or moves photos into
       <dest>\<folder>\<sub-folder...>\<person>\<inner folder...>\<photo>
   - a person placed in a folder    -> always that folder
   - otherwise the photo's folder   -> <folder>\<person>\
   - people but no folder           -> ~Uncategorized\<person>\
   - a folder but nobody on it      -> <folder>\~No people\
   - a photo whose folder goes inside people's folders -> ...\<person>\<that folder>\
   - discarded (D)                  -> ~To Be Discarded\   (outside the folders)
   - Sort Later (L)                 -> ~Sort Later\        (only there)
   - neither people nor a folder    -> left where it is
   - Formats, so everything opens everywhere ("Convert other formats", on by default):
       real JPEG ............................. stays as it is
       WebP / HEIC / AVIF / BMP / TIFF photo .. JPEG, quality 95 (also when misnamed .jpg)
       see-through pictures, screenshots,
       graphics .............................. PNG (lossless)
       PNG ................................... stays (a PNG named .jpg just gets .png)
       animated GIF / WebP, RAW .............. stay as they are
     What's really inside the file decides, not its name. Each new file is checked before
     anything moves; when moving, the original goes to ~To Be Discarded. Copying leaves
     the originals alone. Tags, faces and folders carry over.
   - RAW photos: right-click > "Make a JPEG copy" puts a full-quality JPEG next to the RAW
     (camera white balance, date taken kept), tagged and filed like the RAW. Duplicates
     leaves these copies out.
   When moving:
   - a photo with several people is moved to the person with the biggest face in
     it; the other people's folders get a copy
   - photos with nobody named are left where they are
   - .xmp sidecar files move with their photo
   - FaceSort follows the move, so names, tags and discards stay attached, and the
     destination is added to your photo folders if it isn't one already
   - a "FaceSort moves <date>.csv" list of every original and new location is saved
     in FaceSort's data folder (in "move logs"), in case you want to put anything back -
     your photo folders stay clean
   - running it again only handles new photos; ones already sorted stay put (a photo
     whose place hasn't changed is skipped straight away, without reading the file)
   - Cleanup: afterwards, folders left empty in your photo folders are moved into
     ~To Be Discarded (a name already there gets " (2)" added). A folder with any
     other file in it - text, html, anything - stays where it is. Windows' hidden
     Thumbs.db / desktop.ini don't count (they go along with the empty folder).
     Your photo folders themselves are never moved.

Opening FaceSort no longer scans by itself; click Scan when you want new photos picked
up, or turn on Settings > "Look for new photos every time FaceSort opens".

Confirming is instant: the thumbnail updates straight away and the rest of the window
(left lists, counts, new suggestions) catches up a moment after you pause, so pressing
C quickly never waits. In the preview, the main window updates when you close it.

Thumbnail cache: whole-photo thumbnails and big face crops are saved in
%LOCALAPPDATA%\FaceSort\thumbs, so large libraries open quickly the next time (Settings
shows its size and can clear it). Tested with a 100,000-photo / 125,000-face library:
the window opens in about 1.5 s and the lists update in about 1 s after each change.

Photo formats: JPEG, PNG, HEIC/HEIF, TIFF, WebP, BMP, AVIF, GIF, JPEG 2000, PSD, TGA and
camera RAW files - Canon .cr2/.cr3/.crw, Nikon .nef/.nrw, Sony .arw/.srf/.sr2, Fujifilm
.raf, Olympus/OM .orf, Panasonic .rw2, Pentax .pef, Samsung .srw, Leica .rwl, Sigma
.x3f, Hasselblad .3fr/.fff, Phase One .iiq, Adobe .dng and more. RAW files use the
camera's built-in preview (fast); names go into an .xmp sidecar next to the RAW file,
which Lightroom and digiKam read. If you keep RAW + JPEG pairs, both are listed.

Videos
- Videos (.mp4 .mov .m4v .mkv .avi .wmv .webm .3gp .mts .m2ts .mpg) are scanned like
  photos: FaceSort looks at 7 moments spread through each video (skipping black or
  blurry frames), finds the faces, and keeps each person once - their clearest moment.
- Thumbnails have a thin orange border for videos (plus a small play badge) and a
  thin blue border for photos.
- In the preview, a row of time buttons ("0:12", "1:05") switches between the moments
  with faces; name, confirm, discard etc. work as usual. "Open in default app" plays it.
- Settings > Videos:
    Scan videos after all photos are done (recommended) - photos come first
    Scan videos together with photos
    Only when I click "Scan videos" - a Scan videos button appears in the toolbar
- Names go into an .xmp file next to the video (digiKam, Lightroom and others read it;
  Windows Explorer's tags: search doesn't). Sort into folders copies/moves videos and
  their .xmp like photos. Tattoos can only be marked on photos.
- A video takes about 1-3 s to scan with Standard and ~5-7 s with High accuracy on a
  CPU (faster with a graphics card).

Drag and drop
- Drag photos, videos or whole folders from Explorer onto the FaceSort window. A box
  asks right away how to tag them, before anything is scanned:
    People    - who's in them (several names: separate with commas). Leave empty if
                they're mixed. For a folder named like someone, one click uses the
                folder name.
    Folder    - optional (a dropped folder's name is offered)
    Use the folder names? (when you drop folders) - keep a tree you've already
                organized. Pick which level is the person, e.g. for
                Models\Jane Doe\Beach 2023\a.jpg choose "Level 2 is the person":
                  Models     -> a folder, and Jane Doe is placed in it
                  Jane Doe   -> the person tag (one-face photos get that face named)
                  Beach 2023 -> one of Jane's own folders, given to the photo
                Photos sitting above the person level just get their folders. "Keep
                the folder structure" turns every level into folders, with no person.
                A preview shows how one of your files would be saved.
    Sort Later - put them out of the way until you're ready
  "Tag and scan" applies that to every photo/video as it's scanned; "Just scan" adds
  them without tags.
- Folders are added to your photo folders. Single files from elsewhere are scanned
  where they are (Settings lists them and can remove them).
- Everything you drop is imported in full and first - the import limit doesn't apply
  to dropped files (the rest of your library still follows the limit). Faces are still
  found and grouped as usual.
- Photos already in your library get the tags straight away.

Import limit (toolbar)
- How many new files each Scan imports. 0 = no limit.
- With a limit, Scan imports that many, then groups faces and makes suggestions, and
  stops - the status bar shows how many are still waiting. Click Scan again when you're
  ready for the next batch. With a limit set, FaceSort doesn't import anything by
  itself when it starts.

1.57.2
- Animated GIFs can be edited. Open one in the viewer and press E (or Edit):
  - Every edit-mode tool works and is done to every frame: crop, turn, flip, straighten, light,
    color, white balance (with the eyedropper), color pop, vignette, sharpen and smooth noise.
  - The new "Animated GIF" section at the top of the panel:
    - First / last frame - keep only part of it ("◀ Here" uses the frame on screen). The kept part
      is marked on the bar under the picture.
    - Speed: ¼× to 3×.
    - Play backwards, and Bounce (forwards, then back).
    - Save every frame as a picture… - each kept frame as its own PNG (see-through kept), with your
      edits, in a "<name> frames" folder next to the GIF. The frames aren't added to FaceSort
      automatically, so a long GIF doesn't fill your people with near-identical pictures.
  - The bar under the picture picks which frame you see while editing (it doesn't play while you
    edit).
  - Save copy makes "<name> (edited).gif"; Replace original works as it does for photos.
  - Fix & restore (red eye, restore, upscale, touch-up…) is for single photos: save the frames to
    use it on them.
- The turn buttons (in the viewer and the gallery) now turn animated GIFs - every frame, colors kept.
  Before, they were refused.

1.57.1
- The browser and the gallery watcher are now one window, "Gallery Watcher" (the toolbar button
  that was "Browser"):
  - Along the top: back / forward / reload, the web address, "Watch this page", "Fill login" and
    "Logins".
  - Left: the browser. A second tab, "Being checked", shows the gallery a check is reading (it has
    a ● while a check runs).
  - Right: your watched galleries - Add / Edit / Remove / Open, Check all now / Check selected /
    Stop, how often to check, where downloads go, and Site settings… (was "Sites…"), with what each
    check found underneath. Double-click a gallery to open it in the browser.
  - Check galleries (beside Quick review) still checks them all and opens the same window.
- "Read:" now lists the site's own Max Image Saver reading for sites that have one, and new galleries
  on those sites use it unless you pick otherwise:
  - ModelMayhem: the whole portfolio + credited photos (MM+) - now works in the gallery watcher too.
  - SuicideGirls: the set, or every set on a model's page (Download all sets).
  - Patreon: the post's whole gallery, full size (P+).
  - Flickr, YouPic and Model Society: their own readings (largest size, date taken, whole gallery).
  The list follows the address you type, and Site settings… shows the same choices for each site.
- Fixed: the names MM+ reads from photo credits weren't written into the pictures FaceSort's browser
  saved (Chrome's extension writes them as the photo's keywords). They are now, so the people are
  tagged when the pictures come in.

1.57.0
- Suggested folders: in a person's gallery, the photos of theirs that aren't in one of their
  folders are grouped by the folder they're in on disk, and each of those is suggested as a folder
  for them - in orange, like other suggestions ("Holiday 2019 · 46?"), in the gallery and in the
  folder bar under "Suggested folders". One photo is enough, and the folder's own name is kept even
  when it's generic (DCIM, 2019…), so what you had sorted already stays sorted.
  - ✓ makes the folder for that person (or uses the one of that name they have) and puts the photos
    in it. ✕: not a folder - it isn't suggested for them again. Right-click for "Rename and make…"
    and to open the folder on disk.
  - Right-click photos in a suggestion: put just those in it, or "Not part of …" (they stay in Not
    in a folder).
  - Only FaceSort's folders change; nothing moves on disk until you Sort into folders.
- The two search boxes work on their own now: the one over People / Folders / Albums / Smart Groups
  only filters those lists (Smart Groups too), and the gallery's "Filter by file name" box only
  filters the gallery.

1.56.1
- Fixed: a big download (a 900-picture gallery) only partly showed up in FaceSort - what you drop or
  download was held back by the toolbar's Import limit, and the rest waited for the next Scan.
  Drops and downloads are now always added in full. Two drops while FaceSort was busy also only
  scanned the second one; now both are.
- Gallery watcher: the gallery's name is who its pictures are tagged as (it fills in "Tag them
  as" - type other names there to change it). Opening a gallery that has no names to tag fills
  in its name too.

1.56.0
- FaceSort has its own web browser (Browser, on the toolbar).
  - It keeps you signed in: your session is saved, and when you sign in to a site FaceSort offers to
    save the login - encrypted on this PC (with your profile's key when it has a password, else
    with Windows' protection for your account). "Fill login" fills it in on that site; it's never
    filled in anywhere else. Logins… lists them (Add / Forget).
  - Max Image Saver runs inside it, exactly as in Chrome: the download button on every picture
    (largest version), and the ALL / GALLERY / Flickr / SuicideGirls / Patreon / ModelMayhem /
    YouPic buttons. What it saves goes into a "Web drops" folder and Recently dropped.
- Gallery watcher: watch galleries, albums, profiles or model pages for anything new.
  - Add an address, or "Watch this gallery…" in the browser. Each gallery is read like the ALL
    button (the pictures on the page) or the GALLERY button (each thumbnail's own page), with the
    page scrolled to the end first so everything loads.
  - Check galleries (beside Quick review) checks them all now; the Gallery watcher can also check
    by itself every so many hours while FaceSort is open. You watch each check happen in its window.
  - Only pictures and videos new since the last check are downloaded (into "Web galleries" in your
    library, in the folders Max Image Saver makes - e.g. Flickr/<owner>/<album>), with the date
    taken written into the file where the site gives it.
  - Sites…: how each site is read and tagged - the people to tag, a FaceSort folder, Sort Later,
    or "ask me each time". A gallery can have its own settings instead of its site's.
  - A gallery that needs signing in: FaceSort uses the browser's saved session, or fills in and
    sends your saved login for that site when the page asks for it.
- Fixed: "Lock after X minutes" and the helpers easing off while you work now go by Windows' own
  last-input time (the way 1.55 watched for input could crash together with the browser).

1.55.3
- Fixed for real: Back up now on a library over 2 GB still stopped with "OverflowError" - the
  background job sent its progress in bytes, and Qt's signals only carry numbers up to about
  2 billion. Big counts are now sent as thousandths (any job, not only backups).
- Fixed: a backup cut off in the middle (FaceSort closed while it ran) left its lock behind, and
  every backup for the next hour refused with "another backup is being made right now". FaceSort
  now checks whether that backup is really still running.

1.55.2
- Backups of a big library are much quicker and lighter:
  - The library is copied in one go. Before, SQLite started the copy over every time FaceSort wrote
    to the library meanwhile - on a big, busy library it could keep starting over.
  - Fast compression, or none when it wouldn't help (face pictures, fingerprints and encrypted
    libraries hardly shrink - the old, slow compression spent ages for a few percent).
  - Read, check-summed and written in one pass, with progress in MB and a working Stop.
  - The backup on close runs at low CPU and disk priority, so it doesn't slow the PC down.
  - Fixed: Back up now on a library over 2 GB stopped with "OverflowError" (its progress bar
    counted bytes, and a progress bar only goes up to about 2 billion).

1.55.1
- The release build now checks that encryption works in the packaged app before it ships.

1.55.0
- Settings has a new Privacy tab. Everything here is off unless you turn it on.
  - Password: set one, change it, or remove it. When you set it you're given a recovery key
    (25 characters) - write it down or save it off this PC: if you forget the password, it's the
    only way back in. "New recovery key…" makes a fresh one (the old one stops working).
  - Locking: ask for the password when FaceSort opens, lock after so many minutes without use,
    lock when minimized, and Lock now (Ctrl+Shift+L). Locking hides every FaceSort window until the
    password is typed; FaceSort always locks when it closes.
  - Encrypt the library: everything FaceSort keeps about your photos - names, faces, tags, folders,
    albums, history, thumbnails, settings and the never-track list - is encrypted on this PC and can
    only be read with your password or recovery key. Paths and file names are kept out of the logs
    (and the move logs aren't written). Backups of an encrypted library are encrypted too; when you
    turn it on, FaceSort offers to delete the older, unencrypted backups. Turning it on or off
    happens when FaceSort restarts (each database is copied, checked table by table, then swapped
    in). Encrypting the photo and video files themselves comes in the next version.
- The groundwork for more than one profile (each with its own library) is in place; adding and
  switching profiles comes later.

1.54.0
- Edit mode in the viewer: press E (or the blue Edit button) on a photo or a video.
  Photos - a panel beside the picture, with a live preview:
  - Crop & rotate: turn 90°, flip, straighten (no empty corners), crop shapes (Free, Original, 1:1,
    4:5, 3:2, 16:9) or draw the crop on the photo.
  - Light: exposure, brightness, contrast, highlights, shadows, and Auto.
  - Color: white balance (with an eyedropper - click something white or grey), tint, saturation,
    vibrance, and B&W.
  - Color pop: one color is kept and everything else turns grey - pick reds, oranges, yellows,
    greens, cyans, blues or purples, or click the color with the eyedropper; Sensitivity sets how
    many shades of it are kept.
  - Effects: vignette, from -100 (completely black) through 0 (none) to +100 (completely white).
  - Detail: sharpen and smooth noise.
  - Fix & restore: the photo tools (red eye, auto enhance, restore, smooth blotches, colorize,
    touch-up and face clarity brushes, upscale) - the rest of your edit goes on top.
  - Undo / Redo (Ctrl+Z / Ctrl+Y), Hold to compare (or hold \), Reset.
  - Save copy is always the default ("<name> (edited)" next to the original, with the same people
    and tags). Replace original… is in the Save menu: the original is kept as "<name> (original)"
    and marked to discard, so it goes to ~To Be Discarded when you Sort into folders.
  Videos - trim (Start here [ / End here ]), cut out parts in the middle, turn 90°, low light
  (Off / On / Strong), Steady and frame rate, all saved in one go as a new clip, exact to the frame
  (re-encoded as H.264). Best frames as photos is there too. The original is never changed.
- The viewer: the Info button sits just left of full screen; ⋯ has the photo tools, Rename…,
  Export… and the open buttons. E now opens edit mode - low light on a video moved to Shift+E.
- Settings has a Viewer tab: slideshow speed, face boxes when the viewer opens, the copy name, JPEG
  quality, whether edited copies keep the people and tags, start playing videos when they open,
  speed back to 1× for each video (or keep it), and the saved clips' quality.
- Drag a picture from a web browser onto FaceSort: it's saved in a "Web drops" folder in your
  library and goes into Recently dropped like any drop. FaceSort uses the file the browser hands
  over; if there's only the picture's web address, it downloads that one picture (a site that needs
  you to be signed in may refuse - then the picture as the browser showed it is used, if it came
  along). Only pictures and videos are kept.

1.53.0
- Rename… (right-click a selection, a person or a folder): new file names from a pattern made of
  parts - Person, Everyone, Date, Time, Folder, Number, Old name - e.g. "{person} {date} {n}" gives
  "Anna 2024-07-15 001.jpg". A preview shows every new name before anything changes. Numbers can
  follow the date taken, the gallery's order or the old names; you choose where they start and how
  many digits. Files stay in their folders, their .xmp / Google Takeout .json / .aae files are
  renamed with them, a name that's taken gets (2), and History can undo the whole rename.
- Sort into folders can rename the files as they go (tick "Rename the files as they're sorted" -
  it's never ticked to begin with). Numbers count up in each person's folder, and {person} is the
  person whose folder the file goes in.
- Export… (right-click a selection, a person or a folder): copies in a folder or one zip file, at
  the original size, a percentage, or fitting inside a width × height (never made bigger). Strip
  metadata is on to begin with and remembers your last choice: camera, date, GPS location and the
  names / tags written in the files come off (the color profile stays). A JPEG or PNG that isn't
  resized isn't re-compressed. Videos come along - made smaller in a zip (H.264, at most 1280 px),
  copied as they are in a folder. RAW, HEIC, WebP… become JPEG. The originals are never touched.
- Contact sheet… (right-click): the photos on pages with a title - the person's name - in any font
  installed on your PC (bold / italic, with a preview). A4 or Letter, portrait or landscape, as many
  across as you like, file names under the photos, light or dark. Saved as a PDF or as JPEGs.
- Videos: click anywhere on the position bar and the video jumps there (you can keep dragging).
  The volume and mute are remembered between videos and the next time FaceSort opens.
- "Colour" is spelled "Color" everywhere in FaceSort now.

1.52.1
- Fixed: when two parts of FaceSort opened an older library at the same moment, one could fail
  while bringing it up to date ("duplicate column name"), and a background job then stopped.

1.52.0
- Library backups. Settings has a new Backups tab:
  - A backup is everything FaceSort knows - faces, names, groups, tags, albums, rules, never-track
    list, history and settings - in one compressed file (your photos themselves aren't in it; they
    belong in your normal file backup). Backups go to Documents\FaceSort backups unless you pick
    another folder (another drive or a synced folder is best).
  - How often: every time FaceSort closes (the default for now), once a day, once a week, or only
    when you click Back up now. A backup on close runs by itself after the window has gone, so closing
    isn't slower, and none is made when nothing changed. The newest 10 are kept (you can change it).
  - Each backup is checked: every file in it has a checksum, and a damaged one is refused.
  - Restore…: pick a backup from the list (or Restore from a file…). FaceSort restarts and puts it
    in place before opening; your library as it was is kept first in "Before restore <date>" in
    FaceSort's data folder, so a restore can't lose anything. If the backup's photo folders aren't
    where they were (a new PC, a new drive letter), FaceSort asks where they are now and fixes every
    path - no rescanning.

1.51.3
- Quicker (from the speed report):
  - Picking several date / location buckets in Smart Groups worked them all out again for each
    one (5 seconds for a few): once per change now.
  - With the folder filter on, the list of photos in the picked folders was rewritten at every
    redraw of the side lists: only when the photos change now.
  - Some things that were meant to be kept between redraws (each picture's details for sorting,
    what the duplicate sets depend on, the folder filter's photos) were thrown away at every redraw
    of the side lists by mistake - they're kept now.
  - The name box made 300 round face icons again at every letter typed: each is made once now.
  - Opening an unnamed group goes straight to its faces (it could take a third of a second).
  - After a change that adds something new to the side lists, the next redraw can still be skipped
    when nothing else changed.
- facesort.log: "is gone - now showing" is only written when the gallery lands on a different kind
  of list (the next group after naming one is what's meant to happen, and filled the log).

1.51.2
- Quicker (from the speed report):
  - With a Sort other than Best first, each redraw read the details of every photo in the library
    (68 seconds of freezes in one evening): now only the gallery's own pictures, kept until a scan.
  - Drawing a face box (Tag Person) froze the viewer for 7 seconds while who's who was saved: it's
    saved in the background a few seconds later now.
  - The right-click menu and name box no longer wait for the remembered names (up to 1.7 s).
  - The name box opens without reading every face for each person's picture and photo count.
  - With the folder filter on, which photos are in the picked folders is worked out once, not at
    every redraw; what the duplicate sets depend on is checked in the background.
- Graphics card helper crashes: a job that crashes it now tries the card again without FP16 first,
  and only after a second crash moves to the CPU - remembered for 30 days (it used to start over -
  and crash again - with every update).
- Fixed: "Open window to this folder" on a person did nothing ('set' object is not subscriptable).
- facesort.log: Windows' own file and folder pickers no longer fill it with hundreds of lines of
  harmless "Windows fatal exception: code 0x8001010e" each time one is opened.

1.51.1
- Fixed: a person's empty folders had disappeared from the folder bar above their gallery when
  only some library folders were shown (the folder filter on the left) or Pictures / Video was
  picked. Folder headings stay, empty or not, so you can drop photos onto them - also while the
  file name filter is in use.

Never track (1.51)
- Right-click a face > "Never track this (a logo, watermark, drawing…)…": a permanent "this is
  never a face". It, and every face that matches it closely - in the library now and in every photo
  scanned later - is set aside as not a face by itself, so a site's drawn-face logo stops turning
  up in your groups and suggestions. Faces you've named yourself are never touched.
- Settings > Never track list… shows what's on it (with a picture of each and how many faces it
  set aside); rename or remove an entry - removing can bring back the faces it set aside.
- The list is kept next to your settings, so it survives clearing the library, and it keeps
  working after switching face engines (the same spot is measured again with the new engine).

1.50.6
- "Filter by file name…" box above every gallery (next to Sort): shows only the pictures in that
  gallery - a person's, a folder's, a group's - whose file name or folder name has every word you
  type. Picking something else on the left clears it. (In Smart Groups it works together with the
  side search box.)

1.50.5
- The search box at the top of the side panel: People and Folders are filtered by name as before;
  in Smart Groups the groups stay listed, and the gallery shows only the pictures whose file name
  or folder names (inside the library folder they're in) have every word you typed - "alice" finds
  alice_beach_03.jpg and anything in a folder called Alice. The heading says how many of how many
  are shown. Clear the box to see them all again.

1.50.4
- Other files (notes, links, documents, archives…) now go with a folder's photos however the
  folder was added - in Settings, with Add folder…, by dropping it, or added to it later - not only
  with a drop. When Sort into folders moves ALL the photos out of a folder, its other files go with
  them (into ~files / ~links / ~archives in the person's folder, like a drop's). A folder with a
  photo staying behind keeps its other files. A photo's own .xmp / Takeout .json / .aae / .thm
  travel with it as before; system and hidden files stay.

1.50.3
- Tagging without waiting: while you work in the gallery, the side lists' numbers are read in the
  background about every half second, and the lists are swapped in at your next short pause
  (about a third of a second without a click or key) - never in the middle of a run of keypresses.
  (Before: they waited for 2.5 seconds of quiet, then froze the window while they were redrawn.)
- Parts of the redraw whose numbers haven't changed (a regroup that changed nothing, a change that
  only touched the gallery) aren't redrawn at all.
- Duplicate sets, "not where they belong" and "possibly the same person" finishing in the
  background, and adding photos to / taking them out of an album, go the same quick way.
- Fixed: after an update, the first start showed a generic icon on the taskbar (the installer now
  gives the shortcuts FaceSort's app id and refreshes Windows' icon cache).

1.50.2
- Fixed: folders you dropped in (imported once, not watched) stayed behind, empty, after Sort into
  folders moved their photos out. Now the emptied folder goes to ~To Be Discarded - the whole
  folder, with its empty sub-folders - like empty folders in your library folders. A folder that
  still has anything else in it (a text file, say) stays where it is.

1.50.1
- Smart Groups sorted by Original location: only photos still to sort - nobody tagged on them, no
  folder, not in your sorted library. (A group photo with one person named no longer shows up
  there.) Date taken / Date created are as before.
- Ctrl+A in a side list selects everything in the gallery, not every row of the list.

Quicker side lists (1.50)
- After a change, the numbers the left-hand lists need (each person's faces, the unnamed groups,
  the Smart Groups counts…) are read on a background thread first; the window keeps responding,
  and the redraw then only builds the rows. On a test library of 37,000 photos and 1,362 people
  the freeze after each change went from about half a second to about a seventh of one.
- Also with only some library folders shown (the folder filter).
- People's face counts and list pictures are counted by the database in one pass; looking up a
  row no longer walks every row of every list.
- If you change something while the numbers are being read, they're read again - the lists
  never show older numbers.

1.49.2
- Fixed: emptying ~To Be Discarded into the Recycle Bin, Done checking a dropped batch, and deleting
  a folder, album or smart album sent the gallery to Unnamed group 1. What you're looking at
  stays; when its row is gone, the next row in the same list is shown.
- Graphics card helper crashes: a job that brings it down (an access violation in the graphics
  driver / ONNX Runtime) now runs on a CPU helper instead - slower, but it gets done (it kept
  crashing and the photos never got their tags). The log names the job; it's remembered until the
  next version.
- Fixed: reading poses stopped with "float32 is not JSON serializable" (no poses were saved).
- Moving photos into a folder (folder bar, right-click) redraws the gallery straight away; the
  side lists catch up when you pause.
- The write-ahead log file is cut back to 32 MB after each checkpoint (it stayed at 172 MB).
- Helpers really run at below-normal priority on Windows now, and closing FaceSort ends it at once
  (both Windows calls were quietly failing).

1.49.1
- Fixed: while browsing albums (and after search results), the gallery could jump to Unnamed
  group 1 when the lists were redrawn. The gallery now stays on what you're looking at; it only
  moves on when that row is really gone (to the next one). If it ever does move, facesort.log
  says what it left and where it went.

Possibly the same person (1.49)
- Smart Groups > Possibly the same person: pairs of people who may be one - their faces look
  alike, their names are almost the same ("Jen Smith" / "Jen Smith."), or a generic name
  ("Model 04") looks like someone you've named. Each pair shows a few of each one's clearest faces.
- Merge…: choose the name (either one, or another) and which folder to keep. Faces, whole-photo
  tags, tattoos and their own folders all go to the one person; History can undo it.
- Not the same: remembered, never suggested again. Worked out in the background after
  suggestions change (Look again does it now).
- Fixed: merging two people by renaming one to the other's name no longer loses the folders
  inside the merged person's folder.

Smart albums and shared learning (1.48)
- Smart albums (Albums tab): saved conditions that fill themselves - people (any / all, and
  optionally strong suggestions), tags (any / all, and not these), subject, pose, dates taken,
  a library folder, with or without faces, photos or videos, Safe / Suggestive / Explicit.
  Photos in a locked vault never appear. Right-click: Change, Make a normal album (freezes what's
  in it now), Delete (photos stay).
- Suggested smart albums: people often photographed together, bursts of photos over a few days
  (events), and people often in one scene (Anna · Beach). See a few photos of each, make the
  ones you want, or No thanks - never suggested again.
- Training: a smart album with tags lists, at the end, photos that match everything but one tag
  FaceSort thinks they have. Right-click > Yes tags them, No teaches it they don't.
- Share what FaceSort learned (Settings > Tags): exports your tag models (the built-in content,
  scene and pose tags, plus any of your own tags you tick) and the Safe / Suggestive / Explicit
  model to a small .fslearn file. No photos, faces, people, names, folders, file names or dates -
  only numbers on top of the description model - and nothing learned from vault photos.
  Another FaceSort imports it as a head start (its tags join the tag list); once that FaceSort
  has its own examples the two are blended. Forget imported removes it again.

History and undo (1.47)
- History (toolbar): everything you've done in the last 30 days (and at least the last 5,000
  changes) - naming, confirming, ignoring, discarding, folders, albums and tags, Sort Later,
  renaming / merging / deleting people, what rules did, and files moved by Sort into folders.
  One click or key is one entry.
- Undo any entry, not just the last one (double-click it, or Undo). Anything changed again since
  is left as it is now, and you're told how many. Moved files go back where they were if
  they're still where FaceSort put them. Ctrl+Z undoes the latest change.
- Only what changed is kept (the before and after of those rows, as text).

Add photos, rules and poses (1.46)
- Add photos: one window for dropping files or folders and for Add folder…. By default a dropped
  folder is imported once - what's there now is added and the folder is forgotten (not watched,
  not in your library folders). Tick "Keep watching" to make it a library folder (Add folder…
  has it ticked).
- The folders table: every sub-folder with its photo count and what to use its name for -
  Person, Folder (inside the person above it), Folder of whoever's tagged, Album, or Leave out.
  FaceSort guesses from the names and remembers your choices for that folder next time. An
  example shows where a photo will end up. One Add button; "Add without tagging" is a link.
- Rules for new photos (Settings > Tagging > Rules): when a photo comes from a folder, has
  certain people, is a certain subject (food, landscape…), has a tag or a pose - put it in a
  folder, an album, a tag, or Sort Later. Rules act on photos added after they were made (or
  also existing ones, if you tick that), once per photo, after scans and when suggestions
  change. A suggested person counts when the match is at least 95% (Settings: change or off).
- Suggestions now carry a match strength (50% = just a suggestion, 95% = well clear).
- Poses (Settings > Scanning > Content, offered once): RTMPose (OpenMMLab, Apache) marks 17
  body points on each person; FaceSort works out Standing, Sitting, Lying down, Kneeling, Arms
  raised, Facing away, Full body and Close-up. They're the first tags offered beside a photo,
  and rules can use them. About 90 MB once, on this PC only.


- After updates (a scan, suggestions, background work) the gallery stays on the group or person
  you picked. If that group is gone (all done, say), it goes to the very next one in the list -
  never back to the top.
- The library folders list (top left): click anywhere on a folder to tick / untick it; "All
  library folders" ticks them all, or unticks them all if they all were. Library folders with no
  photos left aren't listed - the last row offers to take them off your library folders (a
  completely empty one goes to '~To Be Discarded').

Duplicates with thousands of sets (1.45.4)
- The Duplicates group froze for up to a minute and a half with thousands of sets: the bar above
  the gallery made a button for every set ("Set 1" ... "Set 5400"). The duplicate sets don't get
  that bar any more, and no view makes more than 300 of those buttons.
- Opening Settings no longer waits while a helper is saving who's who (the remembered people
  count is read without locking the file).

Smoother tagging, update from inside (1.45.3)
- The lists catch up after 2.5 seconds without a click or key (was 1.2), so a run of tagging or
  sorting isn't interrupted by redraws.
- The side list's face counts always read the small summary index, never the face rows (which
  hold fingerprints and thumbnails).
- Every couple of minutes, when you've paused, a helper folds the database's write-ahead log back
  into the library if it has grown big (the helpers write a lot; a long log slows every read).
- Remembered names (right-click, name boxes) are re-read at most every 30 seconds, not each time
  the helpers save who's who.
- Any read on the window's thread that takes over 0.3 s is written to facesort.log with how the
  database ran it - send facesort.log with speed.log if anything still stutters.
- "Update to x.y.z" downloads and installs again for a FaceSort.exe built with Setup.bat: the
  update installs FaceSort in its usual place (same library and settings) and the desktop
  shortcut starts the installed one; later updates then install by themselves.

Scanning on the graphics card helper, FP16 models (1.45.2)
- Scans now run in the graphics card helper: the face models stay loaded from one scan to the
  next, more photos are decoded at once to keep the card busy, and the window stays smooth while
  it scans. A scan you start goes ahead of housekeeping (that job stops and carries on later).
- FP16 models on the graphics card: RTX cards do half-precision maths on their tensor cores much
  faster. Each model is converted once and checked the first time it runs - the same input
  through both, and only if the answers match closely is FP16 used (otherwise that model stays
  in full precision). The verdicts are remembered, and Settings > Scanning / CPU / GPU shows them
  (hover "Faster FP16 models") and can turn FP16 off.
- Checked on real photos: the high-accuracy engines (ArcFace, AdaFace) found the same faces with
  fingerprints matching to 0.9998; the standard engine's small fingerprint model drifts a little
  more in FP16 and normally stays full precision.
- The release build checks the packaged app's FP16 conversion as well as its helpers.

Fix for long freezes (1.45.1)
- 1.45 could freeze for almost a minute after every change on some big libraries: the side list
  asked the database for each group's best face in a way SQLite sometimes ran as a scan of every
  face, once per group. It's back to one plain read (always quick). The people list too.
- Photo details for sorting are re-read only when the photos themselves change, not after every
  tag or name.
- Closing FaceSort on Windows no longer occasionally logs a crash on the way out.

Helper processes (1.45)
- FaceSort's background work now runs in separate helper processes instead of background threads
  inside the window. Python runs one thread's code at a time, so before, however gently the
  background jobs ran, they took turns with the window - the pauses on big libraries. A helper has
  its own Python, runs on its own core, and can't slow the window down.
- One graphics card helper does everything that runs a model: what's in photos (Content), search
  by description, face details and same-shoot matching. It keeps its models loaded between jobs
  and decodes photos on several threads so the card is never waiting for the next picture. Search
  by description now runs on the graphics card too (32 photos at a time on an RTX card).
- CPU helpers do the rest - face clarity (split across all of them at once), duplicates, where
  photos go, suggestions, saving who's who. Settings > Scanning / CPU / GPU > Helper processes: how many
  (Automatic = half the CPU threads, up to 6), or switch them off to use threads as before.
- What you just did goes first; housekeeping eases off while you're clicking.
- Still one writer at a time across the window and every helper (the window first), so no
  "database is locked". A helper that stops unexpectedly can't leave the library locked.
- Helpers end with FaceSort (and on their own if FaceSort is gone). The release build now checks
  that the packaged app's helpers start and work on Windows before it's published.
- The side list's group and folder counts are worked out by the database (fewer pauses).

Quicker on big libraries (1.44.2)
- A person's gallery no longer works out where every photo in the library would be saved after
  each change - just that person's photos (this was most of the pauses at 30,000+ photos).
- The side list counts people's faces in the database instead of one by one.
- "Where photos go" for the folder counts is worked out once you pause, not after every click.
- Opening Settings no longer waits while who's who is saved (it's saved in the background).
- The viewer decodes photos in the background and reads the ones either side ahead, so going to
  the next photo is instant; a big JPEG shows a quick preview while the full picture loads.

Ignore the rest of a group photo (1.44.1)
- Smart Groups > Partially tagged: right-click one or more photos > "Ignore the other untagged
  faces on this photo" (or "these N photos"). Every face box nobody is confirmed on is set aside;
  the people you tagged stay. Bring any back from Smart Groups > Ignored.

Recommended tags, Safe view and No people (1.44)
- Settings > Content (off until you switch it on): FaceSort reads what's in each photo, on this PC,
  with two small models downloaded once (about 30 MB): NudeNet 3.4 (AGPL-3.0) for people and body
  areas, and GantMan's nsfw_model 1.2.0 (MIT) for drawings / neutral / sexy / porn - plus search by
  description (CLIP) when that's on. Each photo is judged Safe, Suggestive, Explicit or Unsure.
- Recommended tags: beside the photo in the viewer, a column of up to 10 tag buttons. Click a tag
  to add it (it turns blue with a tick), click again to take it off. "+ Add a tag..." makes a new
  one. Suggestions come from what's in the photo, the scene (Beach, Studio...), your own tags, and
  tags you've used lately. Settings > Tagging > Tags: edit the list (unticked tags aren't
  suggested), or switch the column off.
- A tag is an album of the same name: tagged photos fill it, and FaceSort learns from it - open
  the album and photos it thinks belong there are at the end under "Suggested for this album"
  (right-click > Add to album or Not for this album; "Add all suggested"). Taking a photo out of
  an album, or a tag off a photo, also teaches it.
- Tags are written into the files as keywords (with Write tags) and read back from them. Search
  finds photos by tag first, then by description. Photos in a locked vault never show up.
- Safe view (toolbar, eye button): blurs Explicit photos everywhere - just the explicit areas in
  the viewer when FaceSort found them, the whole picture otherwise. Settings: also blur
  Suggestive photos.
- Wrong? Right-click a photo or face box > Content... > Safe / Suggestive / Explicit (or the level
  button above the tags). Your choice sticks - it's never changed back - and FaceSort learns from
  your corrections. "Let FaceSort judge again" undoes it.
- Smart Groups > No people: photos with nobody in them (no face, no body, nobody tagged) and not
  sorted yet, split into Food, Landscapes and nature, Text and screenshots, Animals and Other.
  They're no longer listed under No faces found.
- The viewer's recent-folder buttons (next to the folder bar) are gone (Alt+1-5 still works).
- Dragging a box around a single untagged face selects it without opening the People bar - only
  clicking the face box itself does.
- FaceSort is now licensed under the GNU AGPL-3.0 (see LICENSE), as NudeNet requires.

Drops scan only what you dropped (1.43.2)
- Dropping folders or photos on the window scans just those: the rest of your folders isn't
  walked, checked or imported first (new photos elsewhere wait for your next Scan), and the
  library-wide catch-up work (duplicate close-ups, AI detector) is left for later too - so the
  dropped photos are in and tagged as soon as they're read.

Duplicates always listed (1.43.1)
- Smart Groups > Duplicates is always there ("0 sets" when none were found), so you can see the
  duplicate check is on. FaceSort checks new photos in the background.
- Fixed: the duplicate list could keep being postponed while you were busy (each redraw, and
  each photo you stepped to in the viewer, restarted its countdown), so new duplicates could
  take a long time to show up.

Correct men / women, and FaceSort learns from it (1.43)
- Right-click a face (in the gallery or on a face box in the viewer) > Looks... > Looks
  female / Looks male. It moves out of (or into) Men straight away, and FaceSort's estimate
  never changes it back. "Let FaceSort judge again" undoes it. Several faces at once work too.
- Right-click a person in the People list > Man / Woman: one setting for all their faces.
- Look-alikes follow: unnamed faces that are surely the same person as a face you corrected
  take the same answer.
- FaceSort learns: once it has at least 15 faces of each kind (your corrections, and the faces
  of people you set as Man or Woman), it trains its own small model on the face fingerprints,
  on this PC, tuned to your photos. Each face then gets two opinions - InsightFace's and
  FaceSort's own; when they disagree, the face goes to Unsure, not Men. The more you
  correct, the better it gets (it learns again a few seconds after you stop correcting).
- "Ignore all" in Men only ignores what's still in Men - corrected faces are safe.

Settings opens at once (1.42.1) - from your speed report
- Settings no longer waits while Windows is asked for the graphics cards' names (that took
  up to 8 seconds): FaceSort asks once, in the background, when it starts.
- The library is read through memory-mapping with a bigger cache, so the side lists re-read
  their indexes from memory after background work instead of from disk.

Smart Groups, reorganised; men / unsure; head angle (1.42)
- Unknown faces: faces nobody is named for (and not like any group) on photos that aren't
  sorted at all - nobody tagged on them, no folder, not in your sorted library. A photo with
  only a folder (a parade, a concert) counts as sorted and leaves the list.
- Partially tagged (new, its own group): group photos - more than one face box - where
  someone is tagged and other faces aren't named yet. (Replaces "Tagged / sorted".)
- Men and Unsure (with Settings > Scanning > Face details): under both groups, the faces
  that look male, and the ones where it's hard to tell (too small, turned away, or the
  estimate wasn't sure). Each has an "Ignore all" button (asked first; undo from Ignored).
  The rest of each group is the faces that look female. It's an estimate, read on this PC.
- Same shoot and Tattoo matches: only photos nobody is tagged on (and with no folder).
- No faces found: photos with no face box at all (a face box you drew counts), nobody
  tagged and no folder.
- Not where they belong: only files that turned up somewhere FaceSort didn't put them -
  added to or moved within your sorted library by hand. Photos FaceSort placed and that
  are simply due to move (tagged differently, folder changed) are counted on the button
  instead: "Sort into folders (12)…" = what the next sort would move or copy, including
  newly tagged photos still in your photo folders.
- Smart Groups > "Other:" orders that list: the usual order, most / fewest first, or A-Z
  (the "Groups:" box still groups the unnamed groups by face, date or location).
- Head angle (Face details): faces turned well to the side need a closer match before
  they're suggested as someone (that's where many wrong suggestions came from), and each
  person's icon is a front-on face where there is one.
- Face details uses two InsightFace models (about 108 MB, downloaded once from InsightFace's
  GitHub page - only those two files from their model pack - and checked; free for
  non-commercial use). They read each face's stored thumbnail; no photo is opened.

Check for updates button (1.41.2)
- The toolbar (top right, beside Settings) has a "Check for updates" button. When a newer
  version is out it turns into the green "Update to x.y.z" button in the same place.

Quicker clicks (1.41.1) - from your speed report
- Ignoring, removing, discarding, tagging photos, confirming same-shoot matches and closing
  the viewer update the gallery straight away; the side lists and counts catch up when you
  pause (each of these used to wait for a full redraw: about half a second per click).
- Where each photo goes (for the folder counts in the side list) is worked out in the
  background - on a big library that took 2-5 seconds after some changes.
- Settings opens at once (the thumbnail cache is counted in the background, and faster).
- Recycle Bin: looking in the ~To Be Discarded folders happens in the background (a sleeping
  or disconnected drive held the window up for 17 seconds).
- Right-click menus and name boxes no longer re-read the known-people file each time.
- Stepping through photos in the viewer no longer re-checks the duplicates for each photo.
- Starting a job (rescan, move...) no longer waits for who's-who to be saved first: that's
  done at the start of the job itself.

Face clarity brush (1.41)
- In the viewer: Tools > Face clarity brush... (or the "Face clarity" switch in the touch-up
  brush). Paint over a face that's soft, blurry or small and let go: it's redrawn clearer by a
  face model (GFPGAN 1.4) on this PC. Strength sets how much of the clearer face is used;
  paint the whole face, or just the part you want sharper.
- The model draws fine detail it judges likely (lashes, skin texture, hair strands): it works
  on faces that are visible but soft. It can't show what isn't there - a covered or turned-away
  face stays as it is. Where no face is found under the brush, the area is sharpened instead.
- The model (about 340 MB) is downloaded once, the first time you use it, from FaceSort's
  GitHub page, and checked against its published checksum. Your photos are never uploaded.
- As with the other tools: a preview first, saved as a copy next to the original.

Touch-up brush (1.40)
- In the viewer: Tools > Touch-up brush... Paint over a spot, a scratch, dust, a date stamp
  or a stray hair and let go: it's filled in from what's around it. Thin marks are blended
  away; wider areas get texture copied from nearby (grass stays grass, a wall stays a wall).
- Wheel zooms (at the mouse), right-drag or Space + drag moves around, Shift + wheel or
  [ ] changes the brush size, hold \ (or "Hold: before") to see the photo as it was.
  Ctrl+Z / Ctrl+Y undo and redo each fill. Untick "Fill when I let go" to paint several
  strokes first and press Fill (Enter).
- Paint a little past the edges of what should go. Big areas over busy detail (a hole across
  folds of a curtain) can come out patchy - undo and fill it in a few smaller strokes.
- Done brings it back to the viewer as a preview, like the other tools: save it as a copy
  next to the original ("... - touched up.jpg"). The original is never changed.

Speed report (1.39.2)
- FaceSort notes every moment the window is slow to respond (a quarter second or more) and
  what it was busy with, in speed.log in the data folder (Settings > General > Speed report >
  Show speed.log). Nothing is sent anywhere; send the file along if FaceSort feels sluggish.
- The people list's face icons are made together (one read for all their pictures) and are
  kept after a scan or a move unless the picture changed, so the first redraw after a scan or
  a move is much quicker on big libraries.

Updates switched on (1.39.1)
- FaceSort now checks github.com/drac0nik/FaceSort for new versions (once a day; turn off in
  Settings > General > Updates). Installed copies show an "Update to x.y.z" button when one
  is published.

Generic names, marked faces and smoother tagging (1.39)
- Your own generic names (Settings > Tagging > Generic names, or right-click a face >
  More generic names > Edit generic names...). Add, rename, remove and reorder them;
  Amateur, Celebrity, Model, Pornstar and Streamer are there to start with ("Defaults"
  adds them back). Ticked names are right in the right-click menu; unticked ones are under
  "More generic names".
- Tag new "Dancer 03" uses the lowest number nobody has. When you rename "Model 03" to
  who she really is, 03 is free again and the next new Model gets it.
- Each generic name has its own folder: "Unknown Dancer", "Unknown Model"... (made at
  the top of Folders the first time).
- Right-clicking a face box you already marked still offers everything: on an ignored or
  "not a face" box, Undo (it becomes an ordinary unnamed face again), Name this face...
  and the generic names; on a named box, also "Change to a generic name". Naming a box
  that was marked "not a face" also tells FaceSort it is a face.
- Smoother tagging: naming a face updates the gallery straight away and the rest of the
  window catches up when you pause. Updating groups and suggestions waits until you've
  stopped tagging for 3 seconds, runs below normal priority and uses at most half the
  CPU (Settings > Scanning / CPU / GPU > "Keep background work gentle"; turn it off for
  full speed). Same-shoot matching only runs when that feature is on.

An installer, updates, and a newer face model (1.38)
- A proper Windows installer: FaceSort-Setup-x.y.z.exe installs FaceSort for the current
  user (no administrator rights), with a Start menu entry and an optional desktop
  shortcut. Nothing to extract and no build step on the PC. Uninstalling leaves the
  library, settings and face data alone (they're in %LOCALAPPDATA%\FaceSort).
- "Update available": FaceSort asks GitHub once a day whether a newer version is out
  (Settings > General > Updates; nothing about you or your photos is sent). If one is, an
  "Update to x.y.z" button appears in the toolbar: it shows what's new, downloads the
  installer, checks it against its published checksum, closes FaceSort, updates and starts
  it again. A copy run from source or built with Setup.bat opens the download page instead.
- Publishing: a GitHub workflow builds the installer on Windows and publishes it whenever a
  version is tagged (see installer\PUBLISHING.txt). installer\build_installer.bat builds one
  locally with Inno Setup.
- New face engine "Best accuracy - AdaFace" (Settings > Scanning > Face engine): the same
  face finder as High accuracy with a newer recognizer, AdaFace IR-101 trained on 12 million
  photos of 600,000 people (MIT licence), built to cope with poor pictures. Tested against
  High accuracy on the same faces it told people apart better on very dark faces (87% of
  matches found at the darkest level tested, against 68%), on tiny faces (84% against 76%),
  and it kept a wider gap between the right person and the nearest wrong one in every
  test; on clear faces both were perfect. It does about 5x the work per face - comfortable
  with a graphics card, slow without one (FaceSort warns before switching). One-time 260 MB
  download, checked against its known checksum. Switching rescans the library once; names
  and everything sorted are kept.

Duplicates: videos and GIFs; purple GIF border (1.37.5)
- The duplicate check finds exact copies of videos and GIFs (same file size, then the same
  contents). Videos count only as exact copies now: before, two different clips could be
  called the same because they started on the same frame (those old matches are forgotten
  once). It also finds exact copies of blank / one-color pictures it used to skip.
- GIFs have a purple border in the gallery (videos orange, photos blue).

People panel (1.37.4)
- The People panel lists just the people: a person's own folders no longer show beneath
  them. They're the sections of that person's gallery and the buttons in the folder bar
  beside it (drop photos there, right-click to rename, move or delete).

Each person's folders are their own (1.37.3)
- Putting a group photo into a folder from someone's gallery now puts it in that folder for
  that person only. Nobody else on the photo gets a copy of the folder any more (before,
  Jane's "Beach 2023" made a "Beach 2023" for Bob too); they're sorted separately, into their
  own folders - or their main folder if you haven't put the photo in one of theirs.
- Moving a photo to another of the person's folders takes it out of the old one (theirs only).
- After updating, FaceSort checks once for folders earlier versions copied this way and
  offers to take the photos out of the copies (and delete copied folders left empty). The
  original folders and the files on disk aren't touched.

Fixes (1.37.2)
- Moving a photo from one of a person's folders to another (e.g. dragged into the wrong one,
  then into the right one) left it in both when more than one person was on it: the other
  people's copy of the old folder was copied straight back. Sort into folders then saved the
  photo in both folders. Now the old folder lets go of it for everyone on the photo. To clean
  up photos this already happened to, drag them into the right folder once more.
- Exact copies of a photo (same file, same faces in the same places) - e.g. a group photo
  Sort into folders saved in each person's folder - now show once in each gallery. The tile
  stands for all the copies: its tooltip lists where the others are, and naming, confirming,
  tagging or setting a folder covers every copy. Nothing is removed. Settings > Scanning:
  "Show exact copies of a photo once" (on).

Ctrl+Shift+N (1.37.1)
- Ctrl+Shift+N makes a new folder where you are, as in Windows Explorer: inside the folder
  you're looking at, in a person's folder from their gallery, a new album among the albums,
  otherwise a new folder at the top of the Folders tab.

The Scan window, and other files (1.37)
- Scan now opens a window first:
  - Import new files only, check the files already in the library (changed, moved or
    deleted), or both.
  - Which library folders (starts with the ones the folder drop-down shows), and folders to
    leave out by name or full path (files already in the library from those stay).
  - Import up to N photos and videos, folder by folder, newest first or oldest first.
  - Include videos (after all the photos, or together with them).
  - Include other files: documents, music, archives... - everything except system files
    (desktop.ini, Thumbs.db and files Windows marks as system). Hidden files are included.
    A photo's own extra files (.xmp tags, Google Takeout .json, .aae, .thm) aren't listed:
    they already travel with their photo.
  - Skip pictures smaller than N KB (icons, web thumbnails).
  - Files no longer there: take them out of the library, or keep them and show the list.
  - Look again in photos where no face was found / in dark photos.
  - After the scan: find duplicates right away, same-shoot suggestions, or a lighter scan
    (one CPU thread) so the PC stays quick while you work.
  - Remember these choices (also used when FaceSort scans on opening).
- Other files are only recorded in the library (what they are, where they are, who they're
  tagged with, which folder they go to) - never opened, archives never looked inside, and
  nothing is ever written into them. They show as file-type icons with their names in
  Smart Groups > Unassociated files, split by their folder like the other lists. Select
  them and press Enter to tag people, A to set a folder (or drag them onto one); they then
  leave the list and Sort into folders moves them exactly like photos (a file tagged
  "Alice" goes into Alice's folder or wherever you placed her). Tagged files also show
  in that person's gallery. Double-click opens one in its usual program - except
  programs, scripts, shortcuts and web pages, which are only shown in their folder.

Fix (1.36.1)
- The self-tests could hang on Windows (and in the app a background job could be kept
  waiting): while the window saved changes in quick succession, Windows kept handing the
  library straight back to the window. Now a background job that has waited a moment
  (1/20 s) gets the next turn. The window still goes first otherwise.
- Writing to the library no longer looks up source lines for the slow-write log on every
  save (that was also behind the "access violation" lines in the test output).

Better face reading, dark photos and same-shoot suggestions (1.36)
- Face clarity: every face now gets a clarity score - how clearly it can be recognised
  (blurry, tiny, very dark or heavily compressed faces score low). Unclear faces need a
  closer match before they're suggested as someone (that's where most wrong suggestions
  came from), count less in look-alike groups, and someone's blurry photos are no longer
  used as examples of them when they have enough clear ones. Galleries show the clearest
  faces first. Faces scanned before 1.36 get their score in the background, from their
  thumbnails (no photo is opened, nothing has to be scanned again).
- Second look at dark photos: dim party, restaurant, evening and backlit photos are also
  searched in a brightened copy. That finds faces buried in the dark, and a face in a dark
  photo is fingerprinted from whichever version reads more clearly (in tests on very dark
  photos: 98% of faces found instead of 90%, and much closer matches to the same person in
  normal light). On by default (Settings > Scanning). After updating, FaceSort offers once
  to look again at the dark photos already in your library; later, Settings > "Look again
  in dark photos". Names are kept.
- Same-shoot suggestions (Settings > Scanning, off until you switch it on): people whose
  face can't be recognised - turned away, blurry, hidden - are matched by their clothes and
  build to someone you confirmed at the same occasion: photos taken within a few hours of
  each other (and, for photos with a GPS position, within a kilometre), or, for photos
  without a date, in the same folder showing the same scene. Clothes never name anyone
  across different days or places. Suggestions appear in Smart Groups > Same shoot with
  the reason ("same clothes as in 3 photos · 12 min apart · same place"); C confirms (the
  face gets the name, or with no face showing the photo is tagged), R rejects (remembered).
  Uses two models from OpenCV's model zoo (a person finder and an appearance model,
  Apache 2.0 licence), downloaded once (about 140 MB) when you switch it on. Everything
  runs on this PC.
- New tests for all three (the tests folder; Setup.bat runs them before building).

One writer at a time, and a test suite (1.35)
- No more "database is locked": everything that changes the library now goes through one
  write gate inside FaceSort. Only one part of the app writes at a time, the window
  always goes first, and background jobs (scans, checks, regrouping, indexing) save each
  change at once instead of keeping the library locked while they read the next file.
  On a 50,000-photo library during a full scan, the window never waited more than a
  fraction of a second to save.
- Any write that holds the library for more than 2 seconds is written to facesort.log
  ("slow database write ... by <where>"), so a slow spot shows up before it causes trouble.
- The program code is split into smaller files by area (gallery, viewer, dialogs,
  albums, vaults, settings, jobs...). Nothing changes in how it works.
- A test suite (the tests folder) checks the write gate, grouping and naming,
  duplicates and the RAW rules, albums, vaults, the photo tools, video copies and the
  window itself, on a small made-up library (your own library is never touched).
  Setup.bat runs it before building, and asks before building if anything fails.
  run_tests.bat runs it any time.
- Fixed: closing FaceSort could log a "closed database" error from a timer that fired
  after the library was closed.

Smart Groups by folder (1.34)
- Every list in Smart Groups (look-alike groups, Unknown faces, Sort Later, Not faces,
  Ignored, Discarded, tattoo matches…) is split by folder path the same way. A list that
  already had sections keeps them: "To confirm  ›  D:\Photos\Beach 2024". Duplicates keep
  their sets.

Unknown faces by folder (1.33)
- Unknown faces (and its Tagged / sorted and Untagged / unsorted parts) is split into one
  section per folder the photos are in, headed by the folder's full path (D:\Photos\2024\
  Beach 2024) with a line across, like a person's gallery, A to Z - so you know which shoot
  an off-angle face came from. The folder bar above the gallery
  jumps between them. Inside each section the clearest faces still come first.

RAW files and duplicates (1.32.1)
- A set of duplicates that includes a camera RAW file keeps the RAW (it's marked Keep),
  even when a JPEG is the same size or bigger.
- A RAW and a JPEG with the same name in the same folder (IMG_0001.NEF + IMG_0001.JPG - the
  camera saved both) are kept on purpose: they're not shown as duplicates.
- Both are on by default; Settings has a tick box for each.
- "database is locked" again: two more background jobs kept the library locked while they
  worked - search-by-description indexing (whenever a file couldn't be read) and reading
  photo dates. Both now do the slow part first and save in one quick step.

Faster duplicate discarding (1.31)
- Discarding a copy (D, Keep this one, Not duplicates, or in the viewer's compare) now
  takes the photo out of the duplicate sets FaceSort already has, instead of working every
  set out again - the result is exactly the same. The gallery updates at once (about 0.1 s
  instead of 0.5 s on a 50,000-photo test library); the left lists catch up when you pause.
- Exact-copy checks remember each file's checksum, so big files (videos especially) are
  never read in full again on every check.

Fixes (1.30.1)
- "database is locked" while tagging or filing photos during a scan: a background job
  could keep the library locked while it worked on its next file (a long video, say).
  Background jobs now save each result straight away.
- An error about "update_compare" when a duplicate check finished while Quick review was
  open.
- Closing FaceSort while a background job was deep in a long file could end with "Fatal
  Python error: Aborted" in the log; it now closes cleanly.

Speed (1.29)
- Look-alike grouping compares a whole block of faces with every group in one go (an
  exact index): the groups and suggestions come out exactly the same as before, faster
  the bigger the library (about 5x at 100,000 faces). Suggestions skip people who can't
  possibly match a face, and only faces whose suggestion or group changed are saved.
- Opening FaceSort on a big library: the list of Unknown faces is counted in a way that
  stays quick (on a 50,000-photo test library the window came up in 1.5 seconds instead
  of 31), face icons in the left list are made once and reused, and a few database
  indexes were added. SQLite is also told how the library's data is spread (once, then
  again when the library has grown a lot) so it picks the quick way to answer.
- The checks after opening (copies, things in the wrong place, search-by-description
  indexing) now run one after another, each only when nothing else is working, instead
  of all starting at once.

Library folders shown (the box under "Find a person…")
- Tick which of your library folders to look at - in a big library, to see only the
  photos from one place. People, groups, folders and albums then count only the photos
  from those folders (and the ones with none there are hidden); galleries and the viewer
  show only those photos. "Other places" = photos dropped in from outside your library
  folders.
- Nothing leaves the library: scans, suggestions, duplicates and Sort into folders still
  cover everything. Tick "All library folders" to see everything again. It's remembered
  (the button turns orange while only some folders are shown).

Face matching slider (next to the search box)
- The same setting as in Settings: Looser gives more suggestions (and more mistakes),
  Stricter fewer, surer ones. Suggestions and groups update a moment after you let go.

Open window to this image / folder
- Explorer opens with the photo selected (all the selected photos when several are
  selected). For a folder, the photos you have selected in the gallery are picked out when
  they're in it.

Left panel (one tall list, four tabs)
- People: Recently dropped, your named people, and the folders with the people placed in
  them (and each person's own folders).
- Folders: just the folder structure, no people - easier for arranging folders (drag
  folders onto folders, right-click to rename, merge or delete).
- Albums: just the albums. Hover over one to see who's in it.
- Smart Groups: everything still to sort - unnamed (look-alike) groups, Unknown faces,
  Sort Later, tattoo matches, duplicates, AI images, No faces found and the rest.
- The tab you were on is remembered. Clicking a person or group from somewhere else (a
  link, a search, naming a group) switches to the tab it's in; while the lists refresh
  you stay on your tab.
- "Sort by:" at the top of Smart Groups: Face (look-alike groups, the default), Date
  taken (by month, from the camera date when there is one), Date created (the file's
  date) or Original location (the folder each photo is in). Pick a month or a folder to
  see its unsorted faces and photos and name them as usual.
- Pick several rows: Ctrl+click, or press on a row that isn't selected and drag over the
  others. They're shown together, each with its own heading. Right-click several groups
  to name them as one person or ignore them all; several people to confirm all their
  suggestions or place them in a folder; several folders to delete them.
- To move a person or folder (onto a folder or a person), click it first, then drag it.
- Right-click a person: "Confirm all N suggestions".

Photos library (Settings)
- Choose the main folder FaceSort sorts your photos into ("Sort into folders" goes there
  by default) and looks after.
- FaceSort keeps a reference file in it, "FaceSort Library.fslib": who is in each photo
  (every face you confirmed, with its position), tags, folders, discards, Sort Later,
  drawn faces, tattoo marks, plus the people's fingerprints and your folder layout - by
  position inside the library. Choose another library and come back later, or open it
  on another PC, and everything is filled in again - nothing to reconfirm.
- Rename it from Settings > Photos library > Rename...: the folder is renamed in Windows
  and FaceSort carries on exactly as before.
- Confirmed faces are now remembered through Clear library too (they used to come back
  as suggestions).

Sort button (above the pictures, in any person, folder or group): Best first (the
default), Date downloaded (newest first - the date the file arrived on this PC), Date
taken (newest first), File name A-Z or Z-A, File size small to large or large to small.
Sections stay together; the pictures are sorted within each one. Your choice is kept.

AI images
- AI-made pictures are tagged, grouped and sorted like any other photo; they just carry an
  "AI" badge on the thumbnail. The "AI images" list in Smart Groups is off by default -
  Settings > "Show an AI images group" brings it back. A wrong one: right-click > Not AI
  in the gallery, or the "Not AI" button at the top of the viewer (shown on AI photos).
- Marked as AI: the file itself says it was made or edited with AI - Stable Diffusion /
  ComfyUI / InvokeAI / Fooocus / NovelAI generation settings, the "AI-generated" tag used
  by Google, Meta, Midjourney and stock sites, Content Credentials (Adobe Firefly,
  OpenAI, Microsoft...), or a generator's name in the file's details. Reliable when
  present - but a screenshot, many editors and most social media / messaging apps strip
  these labels, so a missing label doesn't mean a photo is real.
- Possibly AI: only with Settings > "AI detector" turned on (off by default; one-time
  37 MB download; makes scans slower). A model (CommunityForensics ViT, MIT licence)
  judges the pixels and lists pictures it is at least 90% sure about. It is often wrong
  - heavily edited, filtered or compressed real photos get flagged and newer generators
  get missed - so treat it as "have a look", never as proof.
- Thumbnails of these get an "AI" badge in the bottom-left corner ("AI?" = detector
  only). Right-click > Not AI takes one off the list.
- Sort into folders can put photos marked as AI in "~AI generated" (tick the box).

Smooth confirming
- Confirming is instant. The sidebar counts and suggestions catch up once you pause
  for a moment (about a second without a key or click), and the work behind them
  runs in the background without holding anything up.
- Who's who is saved to the known-people file (and the Photos library's reference
  file) about 15 seconds after you stop, and always when you close FaceSort.

Duplicates (Smart Groups tab, under Other)
- The same picture more than once: exact copies, and copies that were resized,
  re-saved or lightly edited. Each set shows its copies with size and resolution; the
  largest picture is labelled "Keep".
- Strict: frames from the same video, burst shots and photos taken a moment apart
  are NOT duplicates - if anything in the picture moved (a hand, a blink, someone
  walking past), they're kept apart. Look-alikes are compared closely in the
  background ("Checking possible duplicates closely..."); this is done once per pair.
  Heavily edited copies (strong color changes, crops, filters) aren't counted.
- A photo of several people is copied under each of them on purpose, so those copies
  aren't duplicates. Two copies under the same person still are.
- D discards a copy (it goes to ~To Be Discarded when you sort; Restore brings it back).
  Right-click a copy: "Keep this copy" (discards the others in its set) or "Not a
  duplicate" (never put in that set again). "Keep the best of every set..." does them all.
- Photos scanned by older versions are fingerprinted once, during the next Scan.

Recently dropped (top of the People tab)
- Always at the top of the People tab (it says "Drag photos or folders onto the window"
  when it's empty). Photos dropped in before version 1.17 aren't in it.
- Everything you drag onto the window lands here first, one entry per drop, laid out
  the way it will be saved (folder > person > folder) so you can check it's tagged right.
- Dropping a folder with sub-folders: "Keep the sub-folders - inside the folder of each
  person named above" (the default when there are sub-folders) puts e.g. Ashley\Beach
  and Ashley\Party\Night under Ashley. The other choices are still there.
- A name you type when dropping tags the photos. If a photo has exactly one face, that
  face is only SUGGESTED as them (press C to confirm) - so a wrong guess never teaches
  the face matching. Whole-photo tags are never used to recognise faces.
- When it looks right: right-click > Done checking (or the button). Until then, Sort into
  folders leaves these photos where they are (tick "Also sort Recently dropped" to
  include them anyway).

Other files in a drop
- A photo's own extra files travel with it: .xmp tags, Google Takeout .json (its date
  taken and people's names are read in), Apple .aae edits, .thm video thumbnails.
- Everything else that came in the drop - notes, web links, saved pages, documents,
  archives (never opened), anything - is listed at the bottom of its Recently dropped
  entry, with where it'll go. After Done checking, the next Sort into folders files them
  under the person (or folder) their photos go to, in a sub-folder:
      ~links     .url / .webloc shortcuts, .html / .mht saved pages, .lnk
      ~archives  .zip .7z .rar .tar .gz ... (never opened)
      ~files     everything else (.txt, .pdf, .docx ...)
  A file in a folder with no photos goes with the nearest folder above it that has some.
  Right-click one to open it, show it in its folder, or leave it where it is.

Vaults (private photos)
- "Vault" is always at the top of Folders. Drag people, folders or photos onto it like
  any other folder. Right-click it > Lock: you choose a password (the main Vault needs
  one), and everything in it - its photos, people and folders - disappears from every
  list, gallery, name suggestion and search in FaceSort. It stays shown as
  "Vault (locked)". Click it > Unlock (or right-click > Unlock...), type the password to show
  it again; it stays unlocked until you lock it again, also after closing FaceSort.
- Locking only hides things inside FaceSort: the files stay in their folders on disk,
  so you can still open them in Windows if you know where they are.
- More vaults: right-click Folders (or a vault) > New vault... - any name, each with its
  own password, or none (it still hides, it just opens without asking).
- Right-click an unlocked vault > Set where its files go...: Sort into folders then puts
  that vault's photos in that folder (e.g. on another drive) instead of in your normal
  destination. That folder is added to your photo folders so they stay in FaceSort.
  Change or remove the password, or the place, from the same menu.
- Photos added later that belong in a locked vault (a person in it, a photo in one of
  its folders) are hidden as soon as they're scanned. Face recognition still remembers
  vault people, so nothing is forgotten while it's locked.

Unknown faces (Smart Groups tab, under Other) has two parts:
- Tagged / sorted: the photo is already sorted - someone else on it is tagged, it has a
  folder, or the file is already in your sorted library (or a vault's own folder).
- Untagged / unsorted: nobody on the photo is tagged and it has no folder yet.

Not where they belong (Smart Groups tab, under Other)
- When Sort into folders moves photos (a sorted library), every start-up, scan and
  Sort into folders checks the library: photos sitting in a different folder than their
  tags say (left over from an older FaceSort, moved by hand, or tagged differently
  since) are listed here, grouped by the folder they're in, with where they go.
- "Put them back" moves just those photos into the right folders (nothing else in the
  library is touched); folders left empty go to "~To Be Discarded".
- Photos in the library that aren't tagged any more are listed at the bottom: tag them
  (or D to discard) and they can be put back too.
- The sorted library (and any vault's own folder) is always part of the scan, so files
  that FaceSort didn't know about there are picked up and can be sorted again.

Names for people you don't know the name of
- Right-click a face with no name (a face box in the viewer, or one or more faces in
  the gallery) > Tag new "Amateur 01", "Model 01"... - your list of generic names
  (Settings > Tagging). Each time gives the lowest free number, so two different people
  never share a name, and a renamed person's number is used again.
- The new person is saved in the folder "Unknown Amateur", "Unknown Model"... (made at
  the top of Folders the first time). Rename them any time (right-click > Rename) and
  move them to another folder when you know who they are.

Right-click extras
- Photos: "Rescan" looks for faces again in just the selected photos (names are kept).
  "Open window to this image" opens its folder with the file selected.
- People, folders and drops in the left lists: "Open window to this folder".
- ~To Be Discarded: "Move everything here to the Recycle Bin..." sends the photos you
  marked with D, and EVERYTHING inside the "~To Be Discarded" folders (.webp originals,
  empty folders, any other file) - in your Photos library, your photo folders and the
  place you last sorted into - to the Recycle Bin (Trash on a Mac). They leave FaceSort
  too; you can still restore them from the bin. Anything that can't be moved (open in
  another program) is listed.
- Duplicates leave out anything already in a "~To Be Discarded" folder.

Left lists
- Expand / collapse buttons above each list open or close everything in it.
- The Folders tab shows just the folders (it replaces the old "Hide people" box).
- The Ignored and Not faces lists are gone (those faces are still remembered and never
  suggested; "Probably not faces" still shows up when there's something to check).
- Naming faces or tagging photos opens a list of everyone with their face (and where
  they're saved), narrowing down as you type - so "Ashley 1" and "Ashley 2" are easy
  to tell apart. Up / Down picks, Enter takes the highlighted person; the top row makes
  a new person from what you typed.

Settings
- Three tabs: General / Library (Photos library, photo folders, known people,
  thumbnails, where data is kept), Scanning / CPU / GPU (face engine, matching,
  detection, CPU threads, graphics card, when to scan, AI detector, videos) and
  Tagging (tags already on photos, folder hints, writing tags into photos).
  Settings opens on the tab you used last.

Details panel (toolbar > Details)
- A panel on the right with the selected picture's name, folder, size, dimensions,
  dates, people, folders, AI label and duplicates; then, below a line, its EXIF /
  metadata (camera, lens, exposure, GPS position, software, PNG text such as AI
  generation settings; for videos: length, frame rate, codec). Click Details again to
  hide it.

A person's gallery: everything still to confirm is at the top ("To confirm"), whatever
folder it's in. Once confirmed, it moves back into its own folder's section.

Resolution badge: every thumbnail (photos and videos, in every list - Duplicates too)
shows its smaller side in the bottom-right corner, e.g. 1080p for 1920 x 1080. The Sort
button can order by resolution (largest or smallest first).

Gallery buttons: Sort / Viewer, the Sort order, the list's own buttons (Rename, Set folder,
Delete person...), thumbnail size and Details are all in one row above the pictures.
To turn photos: select them and press [ (left, anticlockwise) or ] (right, clockwise), or
right-click > Turn. (The turn buttons in the viewer show ↺ for left and ↻ for right.) In a narrow window the buttons show just their icons (point at one for
its name). In the viewer: Details is with the zoom / turn buttons, the file name shows
under its path, and leaving full screen (F11) goes back to the size you had.

Folder bar and Create Folder
- When the gallery has sections (a person's own folders, a folder with sub-folders,
  several picks), a column between the left lists and the gallery has a button per
  section (folders with a small folder icon): click to jump there. The section you're
  looking at is highlighted as you scroll. Section titles in the gallery are blue.
- Photos marked as AI are grouped at the end of each section under "AI images".
- In a person's gallery, right-click > Create Folder... makes a folder for them (the
  selected photos go straight in). Every folder of theirs shows as a section, even
  empty. Drag photos onto a folder's button in the bar (or right-click > Move to
  folder) to file them there; "Not in a folder" takes them out again.
- In a folder's gallery, Create Folder... makes a sub-folder.

Only to confirm (tick box at the top of the People tab): People shows only the people with
suggestions waiting for you (C to confirm). Untick it to see everyone again.

All media / Pictures / Video (buttons above the gallery): show everything, only photos,
or only videos - in every list. Headings show how many are left under them.

Search by description (the box at the top right of the gallery)
- Type what you're looking for - "beach at sunset", "red dress", "dog", "birthday cake" -
  and press Enter: matching photos (and videos) are shown, best match first, with no tags
  needed. A small AI model (CLIP) does this entirely on your PC.
- The first time, it asks to download the model (about 150 MB, once). After that nothing
  leaves your computer. Your photos are then prepared for searching in the background
  (the status bar shows how far it is; new photos are added after each scan). Searching
  works on the photos already prepared while the rest are done.
- Photos in a locked vault never show up in a search.

Albums (left panel, under Folders)
- An album is a group a photo can be in without moving a file - a photo can be in "Best
  of 2024" and "Portfolio" at the same time. Folders still decide where files are saved.
- Make one: right-click Albums > New album..., or drag photos from the gallery onto the
  Albums heading. Add photos: drag them onto an album, or right-click > Add to album.
- In an album: right-click > Remove from album (the photo itself isn't touched).
  Right-click an album to rename it (the name of another album merges the two) or delete it.

Duplicates button (above the gallery, beside All media / Pictures / Video)
- Shows only the copies of the same picture that are both in the gallery you're looking
  at, each set under its own heading. Greyed out when there are none there.
- In the viewer, V (or the side-by-side button at the top) compares the photo with its
  copy, side by side: size, resolution, date and folder of each, "Keep this one",
  "Discard this one" (nothing is deleted), "Not duplicates", and Next copy when there are
  more. Greyed out when the photo has no copy in the gallery. V or Esc goes back.

Details panel: in the gallery it sits beside the pictures, level with them, so the title,
search box and buttons above run the full width of the window.

Dropping in a folder: "Remember 'Beach 2024' for whoever gets tagged" (the first choice)
keeps the folder name with the photos. Whoever is tagged on them - when you drop them in
or any time later - gets "Beach 2024" as one of their own folders (Jane\Beach 2024\...,
sub-folders inside it too), and the photos are saved there. On a group photo each person
gets their own "Beach 2024".

Folder bar (beside the gallery)
- Drag the line between the folder bar and the pictures to make it wider (it never gets
  narrower than its minimum). The width is remembered.
- Right-click a folder there: Rename / merge, New folder inside, Move into, Delete,
  Leave out of duplicate checks - the same as in the left panel.
- Drag a folder from the bar onto another folder in the bar, or onto a folder (or a
  person) in the left panel, to move it there.
- In a list with headings, the pictures sit a little to the right of their heading so
  the headings stand out.

Group photos and Sort into folders: each person on a group photo gets a copy in their
folder, and the copies keep every name you confirmed (and tags, ignored faces...). Copies
made by older versions that came back as "to confirm" get their names back from the
original automatically (on start-up and after every scan) - exact copies only.

Leaving photos out of duplicate checks
- Right-click selected photos > Leave out of duplicate checks (Check for duplicates
  again undoes it), or right-click a folder (left panel or folder bar) > Leave out of
  duplicate checks. Example: originals from a shoot in one folder, your edits in
  another - leave the edits folder out and they're never counted as duplicates.
- A folder left out covers the folders inside it, and photos added to it later.

Folders (left panel)
Folders are your save layout. Example: Models > Jane Doe > Beach 2023 saves as
Models\Jane Doe\Beach 2023\photo.jpg.
- Drag a person onto a folder: all their photos are saved under it, whatever folder
  the photo itself has. One place per person; dragging again moves them. They're then
  listed inside the folder instead of under People (they show in People again only
  while they have suggestions waiting for you to confirm).
- Drag a folder onto another to nest it, as deep as you like.
- Person folders: each person can have their own folders, listed under them on the
  left and saved inside their folder: Jane Doe\Beach 2023\. Right-click a person >
  "New folder for ...", or drag a folder onto a person. Give a photo one with A (or
  type "Jane Doe > Beach 2023" in the Category bar - names of the people on the photo
  are offered as you type).
- Names only have to differ from the folders right next to them (like Windows), so
  Family\Beach and Trips\Beach, or Jane's and Bob's "Beach 2023", are separate folders
  that never mix.
- A photo of two people in Jane's "Beach 2023": Bob gets his own "Beach 2023" too (made
  automatically) and the photo is saved under each of them.
- Opening a person shows their photos in sections, one per person folder.
- Drag a folder onto the "Folders" heading to make it a normal top-level folder again,
  or a person back out of their folder (dropping a person on "People" does the same).
- Click a folder to see everything saved under it, in sections with a heading for
  each person and sub-folder ("USA > Barack"), in the same order as on disk.
- Right-click a person: move to / take out of a folder, set the folder on all their
  photos, rename, delete. Right-click a folder: new folder inside, move into another,
  make a person's folder a normal one again, add or take out
  people, rename/merge, delete (its photos, people and sub-folders move up a level).
  Right-click the "Folders" heading: New folder.
- Your layout survives Clear library, like names and tags.

Sort Later (L)
- Press L on photos (or right-click > Sort later) to set them aside: they leave every
  other list - people, groups, folders, No faces found - until you take them out.
- Other > Sort Later lists them; L there (or "Take out of Sort Later") brings them
  back exactly as they were. Naming or tagging one in the preview also takes it out.
- Sort into folders puts them only in ~Sort Later\.
- Sort Later opens up (click its arrow) and can be sorted by face on its own: the people
  in it, look-alike groups, other faces, and photos with no face. Name groups, confirm,
  remove faces, tag people - everything works as in the main lists, and the photos stay
  in Sort Later until you take them out ("Take out of Sort Later" / L, or "Take all
  shown out" for the list you're looking at). Then they show up in the main lists with
  the names you gave them.

Your existing folders as hints
- If your photos are already in folders like "Aunt May", faces in the same folder group
  together a little more easily, and when a group matches a folder its name is shown
  next to the group ("Group 3 · Aunt May?") and filled in when you name it.
- Camera folders (DCIM, Camera, Downloads, 100APPLE...) and very big folders are not
  used. Turn it off in Settings if your folders are mixed events.

People list (left):
- green  = every face confirmed; listed A-Z at the top
- yellow = suggestions waiting for you to confirm; listed A-Z at the bottom, and
  move up into the green list once you've confirmed them
- people with no photos in your current folders (e.g. after removing a folder) are
  hidden until a face or tag matches them again. They're still remembered and always
  offered when you type or pick a name.

No faces found (left list, under Other): photos where no face was detected - a face
turned away, too blurry, too dark, or nobody in it. Select photos and press T (or
click "Tag people...") to tag who's in them. Tagged photos show up under that person
(marked "tagged on photo"), get written as tags, and are sorted/moved into that
person's folder like any other photo. T also works on any other photo, e.g. to add
someone who is facing away in a group shot.

Sort / Viewer tabs (above the title): Sort shows face thumbnails, as before. Viewer
shows the whole photo instead of the face (one tile per photo); every key, button
and menu still works the same.
Thumbnail size: slider above the faces, or Ctrl + mouse wheel.
File path: select one photo and its full path shows under the grid (and at the top of
the preview). Double-click the path to show the file in its folder; right-click to
copy it.
Progress for scans and saving has its own bar along the bottom, so long file names
never sit under it.

Viewer (double-click a face or photo)
-------------------------------------
The photo opens right in the main panel (no separate window). Back to the gallery:
Esc, Backspace, or the back arrow at the top left.
- A strip along the bottom shows the photo you're on in the middle, the ones before it
  to the left and after it to the right. Click one to jump to it; the mouse wheel over
  the strip browses. Home / End go to the first / last.
- Zoom: mouse wheel over the photo, + and -, or the buttons at the top right. 0 fits the
  whole photo, 1 shows it at actual size (1:1), double-click zooms in / back out. When
  zoomed in, drag the photo to look around.
- Space starts / stops a slideshow (3 seconds a photo). F11 is full screen.
Videos play right in the viewer (sound included), also older formats: FLV/F4V (Flash),
WMV/ASF, RealMedia (RM/RMVB), DivX/Xvid AVI, MPEG-1/2, VOB, 3GP/3G2, DV, MOD/TOD, OGV...
- Space plays / pauses (the bar under the video has play/pause, a position slider, the
  time, speed and sound). Speed: 0.1x, 0.25x, 0.5x, 0.75x, 1x, 1.25x, 1.5x, 2x or 4x.
- S (or Screenshot) saves the frame on screen as a JPEG next to the video
  ("<video name> - 1m23_45s.jpg"). It's tagged with the video's people and put in the
  video's folders, then scanned like any new photo (its faces are found too).
- Clicking a "moment with faces" button goes back to that still frame (with face boxes).
Suggested names: when the highlighted face is only suggested as someone (waiting for C),
a confirmed photo of that person shows above the shortcut list, to compare with - handy
for people you only know as "Model 07". It uses the free space there (bigger on a taller
window) and goes away once the face is confirmed.

B hides / shows the face (and tattoo) boxes - or the person button at the top of the
viewer - to look at the photo clean; it stays that way until you press B again.
Several faces at once: with the face boxes showing, drag a box round them on the photo
(start the drag off a face; zoomed in, hold Alt while you drag, since a plain drag moves
the photo). Every face mostly inside it is picked (bright blue, "Selected"). Alt+click a
face adds it or takes it away; Alt+drag adds more. Then I ignores them all (strangers in
a group photo, so they don't keep turning up in Unknown faces and groups later), N marks
them "not a face", or right-click one of them for the same. Confirmed, named faces in the
selection are left alone. Esc or a click on the photo clears the selection.
Shift+I ignores every face on the photo with no name (strangers in the background) in one
go and moves on to the next photo; named and suggested faces are left alone.

Low light (box under a video, or E to step through off / on / strong): brightens dark
videos while they play. Made for night recordings (security / dash / phone cameras, .avi
included): the camera's dark floor - with its noise and compression blocks - becomes real
black, lone speckles are removed, the little that's above the floor is stretched to the
full range, then smoothed as hard as the noise needs (a clean dark video is barely
touched) and lifted with a gentle curve. Very dark footage is worked on at a smaller size
(neighbouring pixels averaged - like watching it in a small window - which gives back
in-between levels and halves the noise), then scaled back up. The dark floor is measured
place by place - the far side of a room is darker still - so faint shapes in the deepest
shadows (someone standing in a dark corner) aren't cut off with it. On keeps the blacks clean;
strong sets black a little lower for the most shadow detail (the darkest areas become a
dark grey) - E switches between them. Color, mostly noise this dark, comes from a
smoothed copy with the color cast removed. It works on the decoder's own brightness
levels, so nothing is lost converting to a picture first. Well-lit videos are left as
they are. Screenshots (S) are saved the way you see it.
The save button beside it opens Save an enhanced copy: low light (off / on / strong),
Steady, the frame rate, and the whole video or only the marked section. The copy is saved
next to the video ("<name> - low light steady 30fps.mp4" and so on, H.264 MP4) with its
sound, tagged like the original (Duplicates leaves the pair alone). It runs in the
background with progress at the bottom - Stop cancels it. Right-click a video in the
gallery > Make a low light copy saves a brightened copy of the whole video straight away.
  Steady: evens out brightness flicker and averages the grain over neighbouring frames
    where nothing moves, so moving things stay sharp. Calms the "crawling" noise of dark
    video, and costs almost no time.
  Frame rate: the video's own rate, or 24 (cinema), 25 (PAL), 29.97 (NTSC), 30, 48, 50,
    59.94 or 60 fps - always between 24 and 60. New frames are made by:
    - Motion interpolation: works out how things move and draws real in-between frames -
      the smoothest, but slow (FFmpeg does it on one processor core, so FaceSort cuts the
      video into pieces and works on them side by side, one per core). On dark, grainy
      video it can smear or warp moving things - turn on Steady too, and try a short
      marked section first.
    - Blend neighbouring frames: quick; fast movement looks a little soft.
    - Repeat / drop frames: quickest, no extra smoothness.
Start / end markers: { (or the ⇤ button by the position bar) puts the start marker where
the video is, } (or ⇥) the end marker; the section between them is shaded orange on the
bar. Enhanced copies, best frames and the 12-shot collage then use only that section
(the collage header says which section). Shift+X (or ✕) clears them; they're cleared when
you go to another video.
The photo button next to it (or right-click a video > Save the best frames, brightened)
asks how many, looks through the whole video brightened, and saves the best moments -
the sharpest frames with the most in them, spread out so they aren't all the same moment -
as full-quality brightened photos next to the video ("<name> - low light 1m23_40s.jpg"),
tagged like the video and scanned into the library.

Videos OpenCV can't read (some codecs, odd or damaged files) are read with FFmpeg instead -
the same decoder the player uses - for scanning, thumbnails, screenshots, collages and the
low light tools. Very dark videos are brightened while they're scanned so faces in them
can still be found. A video listed under Unreadable files from before: Try again.

Frame by frame: , (comma) goes back one frame and . (full stop) forward one frame - the
video pauses, so you can find the exact frame for a screenshot (S). Works on GIFs too.
12-shot collage (button next to Screenshot, or right-click a video in the gallery):
12 frames spread evenly through the whole video, black / white / blank screens skipped,
saved next to the video as "<name> - collage.jpg" in a 3 x 4 grid (4 across for a
wide video, 3 across for a tall one), each with its time in the corner, tagged like the
video. Across the top: the file name, the video's size in pixels and its length.
Right-click a video in the gallery > Play video to open it and start playing.

Photo tools (the wand button at the top of the viewer, or W): you see a preview first -
"Hold to see the original" flips back to compare - and nothing is saved until Save copy,
which puts the new picture next to the original ("<name> - restored colorized.jpg"),
tagged like it and added to the library. The original is never changed. Tools add up:
restore, then colorize, then upscale, all in one copy. Discard (or Esc) throws the
preview away. Everything runs on this PC.
  Remove red eye: finds the eyes (the face detector's eye points, and the face boxes
    already in the library) and turns red pupils dark, keeping the catch-light. Only
    strongly red, round spots in the middle of an eye are touched.
  Upscale x2 / x4 / x8 (the new size is listed next to each): Real-ESRGAN, a small
    upscaling model that comes with FaceSort, adds real-looking detail instead of just
    blurring it bigger. x2 is made at x4 and halved (the cleanest x2); x8 is x4 plus a
    fine resize. Saved as PNG, which loses nothing - upscaling can't bring back detail
    that was never there, but it keeps edges and textures crisp. Sizes over 300
    megapixels aren't offered. Uses the graphics card when there is one.
    (Real-ESRGAN realesr-general-x4v3 by Xintao Wang et al., BSD 3-Clause licence.)
  Auto enhance (light): smooths noise (only as much as the photo has), a gentle white
    balance and levels, local contrast, livelier color and a little sharpening.
  Restore old / faded photo: takes out dust specks and fine scratches, removes the fade
    and color cast (each color stretched back to full range), smooths film grain, and
    brings back contrast and color.
  Smooth dark blotches: for dark photos whose shadows look patchy or ringed (a dark JPEG
    keeps only a few shades in its shadows, in 8 x 8 blocks). The shadows are rebuilt as
    smooth gradients - only where it's dark and away from edges, so lit skin, texture and
    outlines stay exactly as they were - with a fine grain so the steps don't come back.
    "Smooth and lift" also brightens the shadows a little (black stays black). Saved as PNG
    (JPEG would put the blocks back). It can't invent detail the file never had.
  Colorize black & white / sepia: DeOldify guesses the colors; only its color is used -
    all the detail comes from your photo at full size. Downloaded once (about 243 MB, from
    GitHub) the first time you use it, after asking; then it works offline. Works best on
    people, skin, sky, trees and clothes; it can't know what color something really was.
    (DeOldify by Jason Antic, MIT licence.)

Animated GIFs play in the viewer too (looping, as soon as you open one): Space pauses,
the slider steps through the frames, speed works the same, and S saves the frame on
screen ("<name> - frame 7.jpg", tagged like the GIF). Faces are found on the first
frame, so face boxes show on that frame only.
Turning photos: [ and ] (or the buttons at the top of the viewer, or the two buttons in
the middle above the gallery for the selected photos) turn a quarter turn left / right.
JPEGs turn losslessly (only the orientation flag changes); PNG, BMP, TIFF, GIF, WebP,
HEIC and AVIF are saved again. What's really inside the file decides, not its name
(a ".jpg" from a website is often a WebP or PNG). Face boxes and names turn with the
photo. Videos and RAW files aren't turned.
Two bars along the bottom are always there:
  People    type a name. If a face is highlighted it gets that name; otherwise the
            person is tagged on the whole photo. Names on the photo show as chips (x
            removes). Click any face to highlight it and start typing.
  Folder    type the photo's folder (one per photo; typing a new one replaces it).
            The current folder shows as a chip, and your 5 most-used folders are
            buttons next to the bar (Alt+1 ... Alt+5). The photo's folder name is
            offered as a suggestion.
While typing:  Tab completes the first suggestion, Up/Down picks another,
               Enter applies, Ctrl+Enter applies and goes to the next photo,
               PgUp/PgDn changes photo, Esc leaves the bar.
               "new person" / "new folder" warns you before you create a new one.
When no bar has the cursor (the list down the left of the photo shows these: the key
letter in white, the rest in gray):
   (Enter) People Bar      C(a)tegory Bar
   (C)onfirm               a suggested name ("yes, that's them")
   (R)emove Face           not this person / take out of the group
   (T)ag Person            drag a box around a face the scan missed, then type the name
                           (FaceSort measures the face inside the box when it can, so it
                           helps recognise them elsewhere; the box is kept through rescans
                           and Clear library)
                           Ctrl + drag draws a face box any time, like Shift + drag
                           for tattoos. Right-click a drawn box to delete it.
   (M)ark Tattoo           drag a box around a tattoo (Shift + drag works any time)
   (D)iscard               the photo goes to "~To Be Discarded" (D again to undo)
   Sort (L)ater            L again takes it out
   (N)ot a Face            knee, elbow, pattern...
   (I)gnore                a stranger
   Left/Right or PgUp/PgDn previous / next photo

Right-click a face box in the preview to unassign the name ("Unassign Jane" - it won't be
suggested as her again), confirm it, change the name, or mark it not a face / ignored.
Right-click face thumbnails in the main window for "Unassign" too.

In the main window the same letters work on the selected faces/photos (the list along
the bottom shows them); T with one photo selected opens it ready to draw a face box.


QUICK REVIEW (toolbar)
----------------------
Opens in the main panel (Esc, the back arrow or Done goes back). The two faces are
shown large and sharp, sized to the space you have.
One yes/no question at a time, chosen to sort the most faces per keypress:
  "Is this group Barack?"      yes names the whole group
  "Same person?" (two groups)   yes merges them (type a name first to also name them)
  "Is this Barack?" (one face)  yes names it
Keys: Y or Enter = yes, N = no, Space = skip, Z = undo. "No" is remembered, and a
"no" to a person also stops FaceSort suggesting them for those faces.
Likely, high-impact questions come first. Photos taken within ~30 minutes of each
other count as a little extra evidence (same event); two faces in the same photo are
never asked about. Quick review is best for people split across several groups and
for leftover faces; for a brand-new person, "Name this group..." in the main window is
still quickest. In tests on simulated 3,000-face libraries it cut sorting time by
about 20% (typical) to 33% (very varied looks), with no wrong merges.

TATTOOS
-------
For people who aren't always facing the camera. In the preview press M (or hold
Shift) and drag a box around a tattoo, then type whose it is in the People bar.
"Find tattoo matches" (in the preview, under Other > Tattoo matches, or on a person)
searches your photos for the same artwork - rotated, a different size, at an angle,
on a curved arm or partly covered all work. Matches show up as suggestions (orange
"?"): C = yes, it's them; R = not their tattoo (it won't be suggested again).
- Every match you confirm also becomes another reference for that tattoo, so later
  searches find more of the harder angles.
- Mark the tattoo in a clear, close photo; mark the same tattoo again from another
  angle to help further. Right-click a mark to remove it.
- Searching again only checks new photos and new marks.
- Settings > Tattoo matching: Looser finds more, Stricter suggests fewer look-alikes.
- Tattoos are only matched against people you marked yourself, on this PC.

TAGS ALREADY ON YOUR PHOTOS
---------------------------
When photos are added (and once for photos already in your library), people tags
already in the files are used:
- people tags with face areas (digiKam, Lightroom, Picasa, Windows Photo Gallery,
  FaceSort) name those faces directly
- "People|Name" style tags always count as a person; plain keywords (e.g. tags added
  in Windows Explorer) count when they match someone FaceSort knows - or every plain
  keyword, if you turn that on in Settings
- the name is tagged on the photo; if face recognition suggests that person for a face
  in it, the suggestion is confirmed; a photo with one face and one tagged name gets
  that face named


KNOWN PEOPLE (recognition that survives clearing the library)
---------------------------------------------------------------
Everyone you name is remembered in known_people.db: up to 150 face fingerprints per
person (numbers only, no photos), chosen to cover different angles and ages.
- Settings > Clear library... wipes scanned photos/faces and rescans; your people are
  recognized again as suggestions, and discarded/ignored faces stay that way.
- Removing a photo folder doesn't make FaceSort forget anyone.
- Settings > Export people... makes a backup / copy for another PC; Import people...
  merges one in. Keep that file private: it can recognize those people.
- Renaming or deleting a person in FaceSort updates the file too.
- Folders, photo tags, discards and tattoo marks are remembered the same way.

Names written with "Write tags" are also read back when photos are scanned, so a
rescanned library comes back already named. Face names saved by digiKam, Lightroom,
Picasa (MWG regions) and Windows Photo Gallery are imported the same way.


FACE ENGINE: HIGH ACCURACY OR STANDARD
--------------------------------------
On first start (and any time in Settings > Face engine) you can choose:
- High accuracy (recommended): SCRFD face finder + ArcFace ResNet-50 recognition
  (InsightFace models, free for non-commercial use; one-time 275 MB download, ~180 MB
  kept in %LOCALAPPDATA%\FaceSort\models). Measured on a labeled face set:
    * partly hidden faces (hand over half the face, band over the eyes) recognised
      38/38 vs 18/38 and 29/38 with Standard
    * faces cut off by the photo's edge are found and recognised far better
    * clearer gap between different people, so fewer wrong suggestions
    * no false faces at the most sensitive setting where Standard found some
  Slower without a graphics card (~0.6 s vs ~0.12 s per photo on a CPU).
- Standard (fast): the original engine.
Switching rescans your library once. Names, tags, folders, tattoo marks, ignores,
"not this person" answers and Quick review answers all stay with the same faces. The
known-people file keeps samples for both engines, so switching back restores them.

NOT A FACE (X)
--------------
Press N on something that isn't a face (knee, elbow, a pattern...). FaceSort learns
from these: lookalikes are set aside under Other > "Probably not faces" (nothing is
deleted - "It's a face" restores one and it is never set aside again). With High
accuracy, detections the engine itself reads only weakly are set aside there too.
In tests, one N mark on a repeating fabric pattern set aside 85% of the rest of it, and
no real faces were set aside by what was learned. (I is "ignore" - for
real faces of strangers.)

FACE DETECTION
--------------
Each photo is checked at two sizes: full detail for small faces in group shots, and a
smaller copy for close-ups and selfies where the face fills the frame. If no face is
found with confidence, the photo is also tried turned left and right, for sideways
photos without a rotation flag.
Settings > Face detection: slide toward "More faces" to also catch blurry, side-on or
partly hidden faces (you may get the odd non-face; press N on those). After
changing it, "Look again in photos with no faces" re-checks just those photos, and
"Scan all photos again" re-checks everything. Names you gave are kept either way.

GPU
---
Face detection and recognition run on your graphics card through DirectML, which
works with NVIDIA, AMD and Intel GPUs (Windows 10/11). Settings shows which one is in
use, and the status bar says "Face engine: GPU (DirectML)" or "CPU". If the GPU can't
be used, FaceSort switches to the CPU automatically.
Opening/decoding the photo files always happens on the CPU (that's what the "CPU
threads" setting controls), so the GPU speeds scanning up but doesn't take over all of
it. Grouping and sorting are fast numeric steps that don't need the GPU.


ABOUT WRITING TAGS
------------------
- JPEG and PNG: written into the file's XMP metadata without re-saving the image
  (no quality loss). "Date modified" is kept.
- HEIC, TIFF, WEBP, BMP: an .xmp sidecar file next to the photo.
- Your existing tags are kept; names you remove in FaceSort are removed next time.
- Prefer not to touch photo files at all? Settings > "Never modify photo files".


TIPS
----
- Different people grouped together? Settings > Face matching toward Stricter.
  Same person split into many groups? Toward Looser.
- Babies and young children are hardest: name a few groups per child.
- If antivirus flags the self-built .exe, use "Run FaceSort.bat" instead.

Face detection: OpenCV YuNet (MIT). Face recognition: OpenCV SFace (Apache 2.0).
