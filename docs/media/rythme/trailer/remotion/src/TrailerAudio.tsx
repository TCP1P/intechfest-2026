import React from 'react';
import {Audio} from '@remotion/media';
import {interpolate, staticFile} from 'remotion';

const HitSound: React.FC<{from: number}> = ({from}) => {
  return (
    <Audio
      from={from}
      src={staticFile('sfx/Gameplay-Argon-normal-hitnormal.wav')}
      volume={0.12}
      name={`Live hit at frame ${from}`}
    />
  );
};

export const TrailerAudio: React.FC = () => {
  return (
    <>
      <Audio
        from={18}
        src={staticFile('sfx/Intro-welcome.mp3')}
        volume={0.62}
        name="osu! welcome"
      />

      <Audio
        from={129}
        src={staticFile('sfx/Menu-button-hover.wav')}
        volume={0.2}
        name="Hover osu! logo"
      />
      <Audio
        from={141}
        src={staticFile('sfx/Menu-osu-logo-select.wav')}
        volume={0.68}
        name="Open main menu"
      />
      <Audio
        from={141}
        src={staticFile('sfx/Menu-osu-logo-swoosh.wav')}
        volume={0.18}
        name="Open menu movement"
      />

      <Audio
        from={171}
        src={staticFile('sfx/Menu-button-hover.wav')}
        volume={0.18}
        name="Hover Play"
      />
      <Audio
        from={186}
        src={staticFile('sfx/Menu-button-play-select.wav')}
        volume={0.64}
        name="Select Play"
      />

      <Audio
        from={219}
        src={staticFile('sfx/Menu-button-hover.wav')}
        volume={0.18}
        name="Hover Multi"
      />
      <Audio
        from={234}
        src={staticFile('sfx/Menu-button-default-select.wav')}
        volume={0.58}
        name="Select Multi"
      />

      <Audio
        from={270}
        src={staticFile('sfx/Menu-button-hover.wav')}
        volume={0.18}
        name="Hover Crown Arena"
      />
      <Audio
        from={288}
        src={staticFile('sfx/Menu-button-default-select.wav')}
        volume={0.58}
        name="Enter Crown Arena"
      />

      <Audio
        from={314}
        src={staticFile('sfx/Multiplayer-player-joined.wav')}
        volume={0.5}
        name="Lobby joined"
      />
      <Audio
        from={382}
        src={staticFile('sfx/UI-ruleset-select-mania.wav')}
        volume={0.52}
        name="Ruleset revealed"
      />

      <Audio
        from={526}
        src={staticFile('sfx/Multiplayer-countdown-tick.wav')}
        volume={0.62}
        name="Countdown 2"
      />
      <Audio
        from={544}
        src={staticFile('sfx/Multiplayer-countdown-tick.wav')}
        volume={0.65}
        name="Countdown 1"
      />
      <Audio
        from={562}
        src={staticFile('sfx/Multiplayer-countdown-warn-final.wav')}
        volume={0.68}
        name="Countdown GO"
      />

      <Audio
        from={566}
        durationInFrames={237}
        src={staticFile('beatmap.mp3')}
        volume={(frame) =>
          interpolate(frame, [0, 4, 219, 237], [0, 0.8, 0.8, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          })
        }
        name="Ji-eun's Sunset — starts on GO"
      />

      <HitSound from={602} />
      <HitSound from={617} />
      <HitSound from={633} />
      <HitSound from={648} />
      <HitSound from={663} />
      <HitSound from={678} />
      <HitSound from={694} />
      <HitSound from={709} />
      <HitSound from={724} />
      <HitSound from={739} />
      <HitSound from={755} />
      <HitSound from={770} />
      <HitSound from={785} />
    </>
  );
};
