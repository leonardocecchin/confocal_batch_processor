//input = getDirectory("");
//output = getDirectory("");

// Dialog to select channels
Dialog.create("Select Amount of Channels");
Dialog.addNumber("Amount of Channels (1-4)", 1);
Dialog.show();
channel_number = Dialog.getNumber();
if (channel_number < 1){
	showMessage("Channel amount not valid", "Please select a channel amount between 1 and 4.");  
}
if (channel_number >4) {
	showMessage("Channel amount not valid", "Please select a channel amount between 1 and 4. If you have more channels to process, kindly ask Klaudia to add more :-)"); 
}

// Dialog to select folders
Dialog.create("Select Folders");
Dialog.addDirectory("Input Folder:", "C:\\Users\\Klaudia\\OneDrive - Politecnico di Milano\\EXP7 HUF Chips 15M softer Matrix\\IF D7\\Input test");
Dialog.addDirectory("Output Folder - Composites:", "C:\\Users\\Klaudia\\OneDrive - Politecnico di Milano\\EXP7 HUF Chips 15M softer Matrix");
Dialog.addChoice("Output Single Channel Images", newArray("Yes", "No"), "No");
Dialog.addDirectory("Output Folder - Single Channel Images:", "C:\\Users\\Klaudia\\OneDrive - Politecnico di Milano");
Dialog.show();
input = Dialog.getString();
output = Dialog.getString();
choice_output_channel = Dialog.getChoice();
output_channel = Dialog.getString();
if (choice_output_channel == "No")
{
	flag_output_channel = false;
}
else
{
	flag_output_channel = true;
}
// Colors array and unwrapping function
var colorArray = newArray("Orange", "Light Blue", "Bluish Green", "Amber",
	"Yellow", "Blue", "Vermillon", "Pink", "Plain Red", "White");
function applyColorLUT(colorname) {
	// Set R, G, B values
	if (colorname == "Orange") {
		r = 230;
		g = 159;
		b = 0;
	}
	if (colorname == "Light Blue") {
		r = 86;
		g = 180;
		b = 233;
	}
	if (colorname == "Bluish Green") {
		r = 0;
		g = 158;
		b = 115;
	}
	if (colorname == "Amber") {
		r = 245;
		g = 199;
		b = 16;
	}
	if (colorname == "Yellow") {
		r = 240;
		g = 228;
		b = 66;
	}
	if (colorname == "Blue") {
		r = 0;
		g = 114;
		b = 178;
	}
	if (colorname == "Vermillon") {
		r = 213;
		g = 94;
		b = 0;
	}
	if (colorname == "Pink") {
		r = 204;
		g = 121;
		b = 167;
	}
	if (colorname == "Plain Red") {
		r = 255;
		g = 0;
		b = 0;
	}
	if (colorname == "White") {
		r = 255;
		g = 255;
		b = 255;
	}
	// Create custom color LUT
	red = newArray(256);
	green = newArray(256);
	blue = newArray(256);
	// Create gradient from black to selected color
	for (i = 0; i < 256; i++) {
	    red[i] = (r / 255) * i;
	    green[i] = (g / 255) * i;
	    blue[i] = (b / 255) * i;
	}
	setLut(red, green, blue);
}
// Default settings
var c1Name = "DAPI";
var c1Color = colorArray[0];
var c1Min = 0;
var c1Max = 300;
var c2Name = "Vimentin";
var c2Color = colorArray[0];
var c2Min = 0;
var c2Max = 600;
var c3Name = "Fibronectin";
var c3Color = colorArray[0];
var c3Min = 0;
var c3Max = 2000;
var c4Name = "aSMA";
var c4Color = colorArray[0];
var c4Min = 0;
var c4Max = 3000;
// Dialog to select settings
settingsDialog();
// Setting dialog (wrapped in a function)
function settingsDialog() {
	Dialog.create("Select settings");
	if (channel_number >= 1) {
		Dialog.addString("Channel 1 Name", c1Name);
		Dialog.addChoice("Channel 1 Color", colorArray, c1Color);
		Dialog.addNumber("Channel 1 Min", c1Min);
		Dialog.addNumber("Channel 1 Max", c1Max);
		}
	if (channel_number >= 2) {
		Dialog.addString("Channel 2 Name", c2Name);
		Dialog.addChoice("Channel 2 Color", colorArray, c2Color);
		Dialog.addNumber("Channel 2 Min", c2Min);
		Dialog.addNumber("Channel 2 Max", c2Max);
		} 
	if (channel_number >= 3) {
		Dialog.addString("Channel 3 Name", c3Name);
		Dialog.addChoice("Channel 3 Color", colorArray, c3Color);
		Dialog.addNumber("Channel 3 Min", c3Min);
		Dialog.addNumber("Channel 3 Max", c3Max);
		} 
	if (channel_number >= 4) {
		Dialog.addString("Channel 4 Name", c4Name);
		Dialog.addChoice("Channel 4 Color", colorArray, c4Color);
		Dialog.addNumber("Channel 4 Min", c4Min);
		Dialog.addNumber("Channel 4 Max", c4Max);
		} 
	Dialog.show();
	
	if (channel_number >= 1) {
		c1Name = Dialog.getString();
		c1Color = Dialog.getChoice();
		c1Min = Dialog.getNumber();
		c1Max = Dialog.getNumber();
	}
	if (channel_number >= 2) {
		c2Name = Dialog.getString();
		c2Color = Dialog.getChoice();
		c2Min = Dialog.getNumber();
		c2Max = Dialog.getNumber();
	}
	if (channel_number >= 3) {
		c3Name = Dialog.getString();
		c3Color = Dialog.getChoice();
		c3Min = Dialog.getNumber();
		c3Max = Dialog.getNumber();
	}
	if (channel_number >= 4) {
		c4Name = Dialog.getString();
		c4Color = Dialog.getChoice();
		c4Min = Dialog.getNumber();
		c4Max = Dialog.getNumber();
	}
	
	if (channel_number >= 1) {
		settings = "Channel 1: " + c1Name + " (Min: " + c1Min + ", Max: " + c1Max + ", Color: " + c1Color + ")\n";
		}
	if (channel_number >= 2) {
		settings = settings + "Channel 2: " + c2Name + " (Min: " + c2Min + ", Max: " + c2Max + ", Color: " + c2Color + ")\n";
		}
	if (channel_number >= 3) {
		settings = settings + "Channel 3: " + c3Name + " (Min: " + c3Min + ", Max: " + c3Max + ", Color: " + c3Color + ")\n";
		}
	if (channel_number >= 4) {
		settings = settings + "Channel 4: " + c4Name + " (Min: " + c4Min + ", Max: " + c4Max + ", Color: " + c4Color + ")\n";
		}
	File.saveString(settings, output + "settings_used.txt");
}
   
list = getFileList(input);
for (i = 0; i < list.length; i++) {
	processFile(input, output, list[i]);
	if (i == 0) // after processing the first image, ask if parameters need to be changed
	{
		Dialog.create("Change settings?");
		Dialog.addCheckbox("Yes, I want to change settings", false);
		Dialog.show();
		if(Dialog.getCheckbox())
		{
			settingsDialog();
			i--;
		}
	}
	close("*");
	}
	
//processing and saving each channel image

function processFile(input, output, filename) {
    path = input + filename;
    s = "open=[" + path + "] view=Hyperstack";
    run("Bio-Formats Importer", s);
    originalTitle = getTitle();
    run("Split Channels");

	if (channel_number >= 1) {
		selectWindow("C1-" + originalTitle);
		run("Z Project...", "projection=[Max Intensity]");
		run("Subtract Background...", "rolling=50");
		setMinAndMax(c1Min, c1Max);
		applyColorLUT(c1Color);
		if (flag_output_channel)
		{
			// Add automatic scale bar
			scalebarsettings = "height=10 font=24 color=White background=None location=[Lower Right] bold overlay";
			scalebarsize = 0.1; // Scale bar width as 10% of image width
			getPixelSize(unit, w, h);
			if (unit == "pixels") exit("Image not spatially calibrated");
			imagewidth = w * getWidth();
			scalebarlen = 1;
			while (scalebarlen < imagewidth * scalebarsize) {
		    	scalebarlen = round((scalebarlen * 2.3) / pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)))) * pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)));
			}   
			run("Scale Bar...", "width=" + scalebarlen + " " + scalebarsettings);
			saveAs("Jpeg", output_channel + originalTitle + "_" + c1Name + ".jpg");
		}
	}
	
	if (channel_number >= 2) {
		selectWindow("C2-" + originalTitle);
		run("Z Project...", "projection=[Max Intensity]");
		run("Subtract Background...", "rolling=50");
		setMinAndMax(c2Min, c2Max);
		applyColorLUT(c2Color);
		if (flag_output_channel)
		{
			// Add automatic scale bar
			scalebarsettings = "height=10 font=24 color=White background=None location=[Lower Right] bold overlay";
			scalebarsize = 0.1; // Scale bar width as 10% of image width
			getPixelSize(unit, w, h);
			if (unit == "pixels") exit("Image not spatially calibrated");
			imagewidth = w * getWidth();
			scalebarlen = 1;
			while (scalebarlen < imagewidth * scalebarsize) {
		    	scalebarlen = round((scalebarlen * 2.3) / pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)))) * pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)));
			}   
			run("Scale Bar...", "width=" + scalebarlen + " " + scalebarsettings);
			saveAs("Jpeg", output_channel + originalTitle + "_" + c2Name + ".jpg");
		}
	}
	
	if (channel_number >= 3) {
		selectWindow("C3-" + originalTitle);
		run("Z Project...", "projection=[Max Intensity]");
		run("Subtract Background...", "rolling=50");
		setMinAndMax(c3Min, c3Max);
		applyColorLUT(c3Color);
		if (flag_output_channel)
		{
			// Add automatic scale bar
			scalebarsettings = "height=10 font=24 color=White background=None location=[Lower Right] bold overlay";
			scalebarsize = 0.1; // Scale bar width as 10% of image width
			getPixelSize(unit, w, h);
			if (unit == "pixels") exit("Image not spatially calibrated");
			imagewidth = w * getWidth();
			scalebarlen = 1;
			while (scalebarlen < imagewidth * scalebarsize) {
		    	scalebarlen = round((scalebarlen * 2.3) / pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)))) * pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)));
			}   
			run("Scale Bar...", "width=" + scalebarlen + " " + scalebarsettings);
			saveAs("Jpeg", output_channel + originalTitle + "_" + c3Name + ".jpg");
		}
	}
	
	if (channel_number >= 4) {
		selectWindow("C4-" + originalTitle);
		run("Z Project...", "projection=[Max Intensity]");
		run("Subtract Background...", "rolling=50");
		setMinAndMax(c4Min, c4Max);
		applyColorLUT(c4Color);
		if (flag_output_channel)
		{
			// Add automatic scale bar
			scalebarsettings = "height=10 font=24 color=White background=None location=[Lower Right] bold overlay";
			scalebarsize = 0.1; // Scale bar width as 10% of image width
			getPixelSize(unit, w, h);
			if (unit == "pixels") exit("Image not spatially calibrated");
			imagewidth = w * getWidth();
			scalebarlen = 1;
			while (scalebarlen < imagewidth * scalebarsize) {
		    	scalebarlen = round((scalebarlen * 2.3) / pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)))) * pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)));
			}   
			run("Scale Bar...", "width=" + scalebarlen + " " + scalebarsettings);
			saveAs("Jpeg", output_channel + originalTitle + "_" + c4Name + ".jpg");
		}
	}
	
	//Merging composite
	
	if (channel_number == 2) {
		run("Merge Channels...", "c1=[MAX_C1-"+originalTitle+"] c2=[MAX_C2-"+originalTitle+"] keep create");
	}
	
	if (channel_number == 3) {
		run("Merge Channels...", "c1=[MAX_C1-"+originalTitle+"] c2=[MAX_C2-"+originalTitle+"] c3=[MAX_C3-"+originalTitle+"] keep create");
	}
	
	if (channel_number == 4) {
		run("Merge Channels...", "c1=[MAX_C1-"+originalTitle+"] c2=[MAX_C2-"+originalTitle+"] c3=[MAX_C3-"+originalTitle+"] c4=[MAX_C4-"+originalTitle+"] keep create");
	}
	
	Property.set("CompositeProjection", "Max");
	Stack.setDisplayMode("composite");   
	
	// Add automatic scale bar
	scalebarsettings = "height=10 font=24 color=White background=None location=[Lower Right] bold overlay";
	scalebarsize = 0.1; // Scale bar width as 10% of image width
	//scalebarsettings = "height=15 font=48 color=White background=None location=[Lower Right] bold overlay";
	//scalebarsize = 0.2; // Scale bar width as 10% of image width
	getPixelSize(unit, w, h);
	if (unit == "pixels") exit("Image not spatially calibrated");
	imagewidth = w * getWidth();
	scalebarlen = 1;
	while (scalebarlen < imagewidth * scalebarsize) {
    	scalebarlen = round((scalebarlen * 2.3) / pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)))) * pow(10, floor(log(abs(scalebarlen * 2.3)) / log(10)));
	}   
	run("Scale Bar...", "width=" + scalebarlen + " " + scalebarsettings);
	
	saveAs("Jpeg", output + "Composite_" + originalTitle + ".jpg"); 
	saveAs("Tiff", output + "Composite_" + originalTitle + ".tiff");
}

showMessage("Task Completed", "All images have been processed successfully :-)");


