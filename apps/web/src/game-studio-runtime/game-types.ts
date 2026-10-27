export type GameRules = { goal: number; timeLimit: number; lives: number; difficulty: number };
export type GameArcadeOptions = { rotation: boolean; shooting: boolean; splitAsteroids: boolean; scoring: boolean; highScores: boolean };
export type GameLevel = {
  id: string;
  title: string;
  brief: string;
  theme: 'pond' | 'space' | 'garden';
  rules: GameRules;
  seed: number;
};
export type GameSpec = {
  version: 1 | 2;
  title: string;
  description: string;
  genre: 'collect' | 'dodge' | 'platformer';
  theme: 'pond' | 'space' | 'garden';
  player: { name: string; color: string; speed: number; jump: number };
  rules: GameRules;
  objects: { collectible: string; hazard: string };
  instructions: string;
  arcade?: GameArcadeOptions;
  progression?: string;
  levels?: GameLevel[];
};

export type GameResult = { outcome: 'level-complete' | 'won' | 'lost'; elapsed: number; score: number; points?: number; reviewedLevelIds: string[] };
