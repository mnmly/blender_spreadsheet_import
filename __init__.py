bl_info = {
    "name": "Geo Data Importer",
    "author": "Simon Broggi",
    "version": (0, 2, 0),
    "blender": (3, 3, 0),
    "location": "File > Import-Export",
    "description": "Import data to spreadsheet for use with geometry nodes",
    "category": "Import-Export",
}

import bpy
import numpy as np
import sys
from bpy_extras.io_utils import ImportHelper
import subprocess
from pathlib import Path

import csv
import json
import itertools

py_path = Path(sys.prefix) / "bin"
py_exec = next(py_path.glob("python*"))

try:
    import geopandas as gpd
    from shapely import geometry, ops
except ImportError:
    print('INSTALLING geopandas')
    subprocess.call([py_exec, "-m", "pip", "install", "geopandas"])

class DataField:
    def __init__(self, name, dataType):
        self.name = name
        self.dataType =  dataType

def read_gpkg_data(context, filepath, data_layer_name, data_fields):
    report_type = 'INFO'
    report_message = ""
    data_name = "imported_data"
    gdf = gpd.read_file(filepath, layer=data_layer_name if len(data_layer_name) > 0 else 0)
    mesh = bpy.data.meshes.new(name="gpkg_"+data_layer_name)
    # add custom data
    add_data_fields(mesh, data_fields)
    enum_maps = {}
    for col in data_fields:
        if col.dataType == 'ENUM':
            unique_val = gdf[col.name].unique()
            d = enum_maps[col.name] = dict(zip(unique_val, list(range(len(unique_val)))))

    for col in data_fields:
        gdf_col = gdf[col.name]
        if col.dataType == 'ENUM':
            v = gdf_col.map(enum_maps[col.name]).to_numpy().astype('uint8')
        else:
            v = gdf_col.to_numpy()
        mesh.attributes[col.name].data.foreach_set('value', v)

    first_geom = gdf.geometry[0]
    v = []
    if 'LineString' in first_geom.geom_type:

        # for each geometry
        # if the geometry is non multi line string, it retrieves coords directly
        # if not we need to iterate over the `geoms` to flatten them into list of coordinates

        linestring_set = [[(c[0], c[1], 0) for c in g.coords] if not 'Multi' in g.geom_type else\
                          list(itertools.chain.from_iterable([[(c[0], c[1], 0) for c in coord] for coord in [geo.coords for geo in g.geoms]]))\
                          for g in gdf.geometry]
        index = 0
        edges_list = []
        for i, ls in enumerate(linestring_set):
            start = index
            end = index + (len(ls) - 1)
            edges = [(i, i + 1) for i in range(start, end)]
            edges_list.extend(edges)
            index = end + 1

        v = list(itertools.chain.from_iterable(linestring_set))
        v = np.array(v).reshape(-1)
        mesh.edges.add(len(edges_list))
        mesh.edges.foreach_set('vertices', np.array(edges_list).reshape(-1))
    elif 'Point' in first_geom.geom_type:
        v = np.array(list(zip(gdf.geometry.x, gdf.geometry.y, [0] * len(gdf)))).reshape(-1)

    mesh.vertices.add(len(v) // 3)
    mesh.vertices.foreach_set('co', v)

    mesh.update()
    mesh.validate()

    file_name = bpy.path.basename(filepath)
    object_name = bpy.path.display_name(file_name)
    create_object(mesh, object_name)
    
    report_message = "Imported {num_values} from \"{file_name}\"".format(num_values=len(gdf), file_name=file_name)

    return report_message, report_type


def read_json_data(context, filepath, data_array_name, data_fields, encoding='utf-8-sig'):

    # return variables for displaying a report
    report_type = 'INFO'
    report_message = ""

    f = open(filepath, 'r', encoding=encoding)
    data = json.load(f)
    
    data_array = data[data_array_name] 
    
    # name of the object and mesh
    data_name = "imported_data"
    
    mesh = bpy.data.meshes.new(name="json_"+data_array_name)
    mesh.vertices.add(len(data_array))
    #coordinates = np.ones((len(data_array)*3))
    #mesh.vertices.foreach_set("co", coordinates)
    
    # https://docs.blender.org/api/current/bpy.types.Attribute.html#bpy.types.Attribute

    # In JSON an empty string is a valid key.
    # Blender mesh attributes with an empty name string dont work
    # That's why an empty key in JSON generates an attribute with the name "empty_key_string"

    # add custom data
    add_data_fields(mesh, data_fields)

    # set data according to json
    i=0
    for k in data_array:

        # make sure it's the right data type
        for data_field in data_fields:
            value = k[data_field.name]
            if(data_field.dataType == 'FLOAT'):
                value = float(value)
            elif(data_field.dataType == 'INT'):
                value = int(value)
            elif(data_field.dataType == 'BOOLEAN'):
                value = bool(value)

            mesh.attributes[data_field.name if data_field.name else "empty_key_string"].data[i].value = value

        mesh.vertices[i].co = (0.01 * i,0.0,0.0) # set vertex x position according to index
        i=i+1

    f.close()

    mesh.update()
    mesh.validate()

    file_name = bpy.path.basename(filepath)
    object_name = bpy.path.display_name(file_name)
    create_object(mesh, object_name)
    
    report_message = "Imported {num_values} from \"{file_name}\"".format(num_values=i, file_name=file_name)

    return report_message, report_type

def read_csv_data(context, filepath, data_fields, encoding='latin-1', delimiter=",", leading_liens_to_discard=0):

    # return variables for displaying a report
    report_type = 'INFO'
    report_message = ""

    mesh = bpy.data.meshes.new(name="csv_data")

    add_data_fields(mesh, data_fields)
    
    with open(filepath, 'r', encoding=encoding, newline='') as csv_file:
        #print("importing {file} without the first {lines}".format(file=filepath, lines=leading_liens_to_discard))
        discarded_leading_lines = 0
        while(discarded_leading_lines < leading_liens_to_discard):
            line = csv_file.readline()
            #print("discarded line " + discarded_leading_lines + ": " + line)
            discarded_leading_lines = discarded_leading_lines + 1

        csv_reader = csv.DictReader(csv_file, delimiter=delimiter)

        error_message = ""
        i=0
        try:
            for row in csv_reader:
                # make sure it's the right data type
                # raises ValueError if the datatype can not be converted 
                for data_field in data_fields:
                    value = row[data_field.name]
                    if(data_field.dataType == 'FLOAT'):
                        value = float(value)
                    elif(data_field.dataType == 'INT'):
                        value = int(value)
                    elif(data_field.dataType == 'BOOLEAN'):
                        value = bool(value)
                    row[data_field.name] = value
                
                mesh.vertices.add(1)
                mesh.update() #might be slow, but does it matter?...

                # assign row values to mesh attribute values
                for data_field in data_fields:
                    mesh.attributes[data_field.name if data_field.name else "empty_key_string"].data[i].value = row[data_field.name]

                mesh.vertices[i].co = (0.01 * i,0.0,0.0) # set vertex x position according to index
                i = i+1
        except ValueError as e:
            error_message = repr(e)
            report_type = 'WARNING'
        except KeyError as e:
            error_message = repr(e)
            report_type = 'WARNING'

        csv_file.close()

        mesh.update()
        mesh.validate()

        file_name = bpy.path.basename(filepath)
        object_name = bpy.path.display_name(file_name)

        # create object if data was imported
        if (len(mesh.vertices) > 0):
            create_object(mesh, object_name)
            report_message = "Imported {num_values} lines from \"{file_name}\".".format(num_values=i, file_name=file_name)
        else:
            report_message = "Import failed. Check if import options match CSV File!"
            report_type = 'ERROR'

        report_message = "{message}\n{error_message}".format(message=report_message, error_message=error_message)

    return report_message, report_type


def add_data_fields(mesh, data_fields):
    # add custom data
    for data_field in data_fields:
        mesh.attributes.new(name=data_field.name if data_field.name else "empty_key_string", type=data_field.dataType if data_field.dataType != 'ENUM' else 'INT8', domain='POINT')

def create_object(mesh, name):
    # Create new object
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

class SPREADSHEET_UL_data_fields(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        custom_icon = 'OBJECT_DATAMODE'
        #item is a DataFieldPropertiesGroup
        #print(type(item.name))
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            #layout.label(text=item.name, icon = custom_icon)
            layout.prop(data=item, property="name", text="")
            layout.prop(data=item, property="dataType", text="")
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            #layout.label(text="", icon = custom_icon)
            layout.prop(data=item, property="name", text="")
            layout.prop(data=item, property="dataType", text="")


# https://blender.stackexchange.com/questions/16511/how-can-i-store-and-retrieve-a-custom-list-in-a-blend-file
# https://docs.blender.org/api/master/bpy_types_enum_items/attribute_domain_items.html?highlight=mesh+attributes

class DataFieldPropertiesGroup(bpy.types.PropertyGroup):
    name : bpy.props.StringProperty(
        name="Field Name",
        description="The name of the field to import",
        default="",
    )

    #  https://docs.blender.org/api/current/bpy.types.Attribute.html#bpy.types.Attribute
    dataType: bpy.props.EnumProperty(
        name="Field Data Type",
        description="Choose Data Type",
        items=(
            ('FLOAT', "Float", "Floating-point value"),
            ('INT', "Integer", "32-bit integer"),
            ('BOOLEAN', "Boolean", "True or false"),
            ('ENUM', "ENUM", "Enums"), # string wont work
        ),
        default='FLOAT',
    )

#todo: add presets
# https://sinestesia.co/blog/tutorials/using-blenders-presets-in-python/

# ImportHelper is a helper class, defines filename and invoke() function which calls the file selector.
class ImportSpreadsheetData(bpy.types.Operator, ImportHelper):
    """Import data to Spreadsheet"""
    bl_idname = "import.spreadsheet"  # important since its how bpy.ops.import.spreadsheet is constructed
    bl_label = "Import Spreadsheet"

    # ImportHelper mixin class uses this
    # filename_ext = ".json;.csv"

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling.

    filter_glob: bpy.props.StringProperty(
        default="*.json;*.csv;*.gpkg",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    data_fields: bpy.props.CollectionProperty(
        type=DataFieldPropertiesGroup,
        name="Field names",
        description="All the fields that should be imported",
        options={'HIDDEN'},
    )

    # The index of the selected data_field
    active_data_field_index: bpy.props.IntProperty(
        name="Index of data_fields",
        default=0,
        options={'HIDDEN'},
    )

    array_name: bpy.props.StringProperty(
        name="Array name",
        description="The name of the array to import",
        default="",
        options={'HIDDEN'},
    )
    
    json_encoding: bpy.props.StringProperty(
        name="Encoding",
        description="Encoding of the JSON File",
        default="utf-8-sig",
        options={'HIDDEN'},
    )

    csv_delimiter: bpy.props.StringProperty(
        name="Delimiter",
        description="A one-character string used to separate fields.",
        default=",",
        maxlen=1,
        options={'HIDDEN'},
    )

    csv_leading_lines_to_discard: bpy.props.IntProperty(
        name="Discard leading lines",
        description="Leading lines to discard",
        default=0,
        min=0,
        options={'HIDDEN'},
    )

    csv_encoding: bpy.props.StringProperty(
        name="Encoding",
        description="Encoding of the CSV File",
        default="latin-1",
        options={'HIDDEN'},
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Import Spreadsheet Options")

    def execute(self, context):
        if(self.filepath.endswith('.json')):
            report_message, report_type = read_json_data(context, self.filepath, self.array_name, self.data_fields, self.json_encoding)
        elif(self.filepath.endswith('.csv')):
            report_message, report_type = read_csv_data(context, self.filepath, self.data_fields, self.csv_encoding, self.csv_delimiter, self.csv_leading_lines_to_discard)
        elif(self.filepath.endswith('.gpkg')):
            report_message, report_type = read_gpkg_data(context, self.filepath, self.array_name, self.data_fields)
        
        self.report({report_type}, report_message)
        return {'FINISHED'}

class AddDataFieldOperator(bpy.types.Operator):
    bl_idname = "import.spreadsheet_field_add"
    bl_label = "Add field"

    def execute(self, context):
        sfile = context.space_data
        operator = sfile.active_operator
        item = operator.data_fields.add()

        operator.active_data_field_index = len(operator.data_fields) - 1
        
        return {'FINISHED'}

class RemoveDataFieldOperator(bpy.types.Operator):
    bl_idname = "import.spreadsheet_field_remove"
    bl_label = "Remove field"

    def execute(self, context):
        sfile = context.space_data
        operator = sfile.active_operator
        index = operator.active_data_field_index
        operator.data_fields.remove(index)
        operator.active_data_field_index = min(max(0,index - 1), len(operator.data_fields)-1)
        return {'FINISHED'}

class SPREADSHEET_PT_gpkg_options(bpy.types.Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "JSON Import Options"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == "IMPORT_OT_spreadsheet" and operator.filepath.lower().endswith('.gpkg')

    def draw(self, context):
        sfile = context.space_data
        operator = sfile.active_operator
        layout = self.layout
        layout.prop(data=operator, property="array_name")

class SPREADSHEET_PT_json_options(bpy.types.Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "JSON Import Options"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == "IMPORT_OT_spreadsheet" and operator.filepath.lower().endswith('.json')

    def draw(self, context):
        sfile = context.space_data
        operator = sfile.active_operator
        layout = self.layout
        layout.prop(data=operator, property="array_name")
        layout.prop(data=operator, property="json_encoding")

class SPREADSHEET_PT_csv_options(bpy.types.Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "CSV Import Options"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == "IMPORT_OT_spreadsheet" and operator.filepath.lower().endswith('.csv')

    def draw(self, context):
        sfile = context.space_data
        operator = sfile.active_operator
        layout = self.layout
        layout.prop(data=operator, property="csv_delimiter")
        layout.prop(data=operator, property="csv_leading_lines_to_discard")
        layout.prop(data=operator, property="csv_encoding")

class SPREADSHEET_PT_field_names(bpy.types.Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "Field Names"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == "IMPORT_OT_spreadsheet"

    def draw(self, context):
        sfile = context.space_data
        operator = sfile.active_operator
        layout = self.layout

        #layout.template_list("UI_UL_list", "", operator, "data_fields", operator, )
        
        # success with this tutorial!
        # https://sinestesia.co/blog/tutorials/using-uilists-in-blender/

        rows = 2
        filed_names_exist = bool(len(operator.data_fields) >= 1)
        if filed_names_exist:
            rows = 4

        row = layout.row()
        row.template_list("SPREADSHEET_UL_data_fields", "", operator, "data_fields", operator, "active_data_field_index", rows=rows)

        col = row.column(align=True)
        col.operator(AddDataFieldOperator.bl_idname, icon='ADD', text="")
        col.operator(RemoveDataFieldOperator.bl_idname, icon='REMOVE', text="")
        
blender_classes = [
    SPREADSHEET_UL_data_fields,
    DataFieldPropertiesGroup,
    ImportSpreadsheetData,
    SPREADSHEET_PT_field_names,
    SPREADSHEET_PT_gpkg_options,
    SPREADSHEET_PT_json_options,
    SPREADSHEET_PT_csv_options,
    AddDataFieldOperator,
    RemoveDataFieldOperator,
]

# Only needed if you want to add into a dynamic menu
def menu_func_import(self, context):
    self.layout.operator(ImportSpreadsheetData.bl_idname, text="Spreadsheet Import (.csv, .json, .gpkg)")

# Register and add to the "file selector" menu (required to use F3 search "Text Import Operator" for quick access)
def register():
    for blender_class in blender_classes:
        bpy.utils.register_class(blender_class)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    for blender_class in blender_classes:
        bpy.utils.unregister_class(blender_class)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

