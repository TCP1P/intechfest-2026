# Rythme teaser composition

This author-side Remotion project edits native client recordings into the
challenge teaser. It does not create a replacement cursor, reconstruct
gameplay, add click rings, or alter the captured osu!lazer interface.

The edit deliberately uses:

- one continuous menu take driven entirely by visible pointer clicks;
- four named, independently previewable scenes in Remotion Studio;
- four-to-five-frame fades only where waiting time was omitted;
- the selected beatmap audio beginning on the match-start frame;
- hover, menu, countdown, and hit sounds packaged with the pinned osu!lazer
  build and synchronized to visible actions;
- a short fade at the beginning and end, with no promotional overlays.

Render from the trailer directory:

```bash
./render-trailer.sh /path/to/menu.mkv /path/to/gameplay.mkv
```

Open the editable timeline:

```bash
cd remotion
npm run studio
```
