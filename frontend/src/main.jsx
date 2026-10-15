import { AppRegistry } from "react-native";
import App from "./App";

AppRegistry.registerComponent("Volume", () => App);
AppRegistry.runApplication("Volume", {
  rootTag: document.getElementById("root"),
});
