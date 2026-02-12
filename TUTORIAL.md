# WhatsApp Baileys Tutorial

Since you are using Python for the backend but **Baileys** is a Node.js library, I have created a lightweight Node.js service (`wa-service`) that acts as a bridge. This service connects to WhatsApp and communicates with your Flask app via HTTP.

## 1. Prerequisites
You need **Node.js** installed on your machine.
- Download it here: https://nodejs.org/

## 2. Setup the Baileys Service
1. Open a terminal and navigate to the `wa-service` folder:
   ```powershell
   cd wa-service
   ```
2. Install the necessary JavaScript dependencies:
   ```powershell
   npm install
   ```

## 3. Run the WhatsApp Service
1. Start the service:
   ```powershell
   npm start
   ```
2. A **QR Code** will appear in your terminal.
3. Open WhatsApp on your phone, go to **Linked Devices** > **Link a Device**, and scan the QR code.
4. Once connected, you will see `opened connection` in the terminal.

## 4. Run the Flask Application
1. Open a **new** terminal (keep the Node.js one running).
2. Navigate to your project folder:
   ```powershell
   cd whatsapp-health-bot
   ```
3. Run the Python app:
   ```powershell
   python app.py
   ```

## How it Works
- **Flask App** (Port 5000): Handles the dashboard, database, and logic.
- **Node.js Service** (Port 3000): Handles the actual WhatsApp connection.
- When Flask needs to send a message, it sends a request to `http://localhost:3000/send-message`.
- When a WhatsApp message arrives, the Node.js service forwards it to `http://localhost:5000/webhook/whatsapp`.
