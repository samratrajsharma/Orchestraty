import { Component, Prop, h } from "@stencil/core";

@Component({ tag: "orc-spinner", styleUrl: "orc-spinner.css", shadow: true })
export class OrcSpinner {
  @Prop() size: "sm" | "md" | "lg" = "md";

  render() {
    return <div class={`spinner spinner--${this.size}`} />;
  }
}
