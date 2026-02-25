import { useState } from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
// import Login from "./components/pages/Login";
// import Signup from "./components/pages/Signup";
// import Dashboard from "./components/ceo/Dashboard";
// import Layout from "./components/Layout";
import SuperAdminDashboard from "./components/superAdmin/SuperAdminDashboard";
function App() {
  const [count, setCount] = useState(0);

  return (
    <>
      {/* <Router>
        <Routes>
          <Route path="/" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
        </Routes>
      </Router> */}
      {/* <Dashboard /> */}
      {/* <Layout /> */}
      <SuperAdminDashboard />
    </>
  );
}

export default App;
