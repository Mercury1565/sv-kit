declare module "netlistsvg" {
  export function render(skin: string, netlist: unknown, done?: (err: unknown, svg: string) => void): Promise<string>;
}
