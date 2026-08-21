import React from 'react';
import {Composition, Folder} from 'remotion';
import {RythmeTrailer} from './RythmeTrailer';
import {LobbyScene} from './scenes/LobbyScene';
import {MenuScene} from './scenes/MenuScene';
import {PlayScene} from './scenes/PlayScene';
import {RevealScene} from './scenes/RevealScene';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Folder name="Editable-scenes">
        <Composition
          id="MenuScene"
          component={MenuScene}
          durationInFrames={306}
          fps={30}
          width={1920}
          height={1080}
        />
        <Composition
          id="LobbyScene"
          component={LobbyScene}
          durationInFrames={72}
          fps={30}
          width={1920}
          height={1080}
        />
        <Composition
          id="RevealScene"
          component={RevealScene}
          durationInFrames={84}
          fps={30}
          width={1920}
          height={1080}
        />
        <Composition
          id="PlayScene"
          component={PlayScene}
          durationInFrames={354}
          fps={30}
          width={1920}
          height={1080}
        />
      </Folder>

      <Composition
        id="RythmeTrailer"
        component={RythmeTrailer}
        durationInFrames={803}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
