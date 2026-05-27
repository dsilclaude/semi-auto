import csv
import tkinter as tk
from tkinter import filedialog, messagebox

def modify_csv(file_path):
    try:
        with open(file_path, mode='r', newline='') as file:
            reader = list(csv.reader(file))
            if len(reader) < 2:
                messagebox.showerror("Error", "CSV file is empty or has no data rows.")
                return
            
            header = reader[0]
            meas_group_index = header.index("Meas Group")
            first_meas_value = reader[1][meas_group_index]
            
            for row in reader[1:]:
                row[meas_group_index] = first_meas_value
        
        output_file = "modified_" + file_path.split("/")[-1]
        with open(output_file, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerows(reader)
        
        messagebox.showinfo("Success", f"Modified CSV saved as '{output_file}'")
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred: {e}")

def open_file():
    file_path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
    if file_path:
        modify_csv(file_path)

# GUI Setup
root = tk.Tk()
root.title("Modify Meas Group in CSV")

tk.Button(root, text="Select CSV File", command=open_file).pack(pady=20)

root.mainloop()
 