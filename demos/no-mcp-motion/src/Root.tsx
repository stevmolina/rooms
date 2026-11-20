import React from "react";
import { Composition } from "remotion";
import { NoMcp, TOTAL } from "./NoMcp";

export const Root: React.FC = () => (
  <Composition id="NoMcp" component={NoMcp} durationInFrames={TOTAL} fps={30} width={1920} height={1080} />
);
