import { useState } from "react";
// import Login from "./components/Login";
import Dashboard from "./components/Dashboard";
// import Layout from "./components/Layout";
function App() {
  const [count, setCount] = useState(0);

  return (
    <>
      {/* <Login /> */}
      <Dashboard />
      {/* <Layout /> */}
    </>
  );
}

export default App;
